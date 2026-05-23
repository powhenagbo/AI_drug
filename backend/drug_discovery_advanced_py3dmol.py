#!/usr/bin/env python3
"""
drug_discovery_advanced_py3dmol.py
===================================
End-to-end drug discovery pipeline:
  - ChEMBL target search and bioactivity retrieval
  - Lipinski descriptor calculation and pIC50 conversion
  - 3-class / 2-class activity labelling
  - PaDEL fingerprint generation (optional)
  - ML model training and evaluation
  - 2D and 3D visualisations (static + interactive py3Dmol)
  - ZIP archive of all outputs

Dependencies (install via requirements.txt):
  pandas numpy chembl-webresource-client rdkit seaborn matplotlib
  scikit-learn scipy py3Dmol
"""

from __future__ import annotations

import logging
import os
import subprocess
import warnings
import zipfile
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # must be set before pyplot is imported

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import py3Dmol
import seaborn as sns
from chembl_webresource_client.new_client import new_client
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs, Descriptors, Draw, Lipinski
from sklearn.decomposition import PCA
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.svm import SVC

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

try:
    from pipeline_db_saver import PipelineDBSaver
except ImportError:
    PipelineDBSaver = None  # type: ignore

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
DEFAULT_DISEASE         = "diphtheria"
ACTIVE_THRESHOLD        = 1_000    # IC50 ≤ 1 000 nM  → active
INACTIVE_THRESHOLD      = 10_000   # IC50 ≥ 10 000 nM → inactive
TEST_SIZE               = 0.2
RANDOM_STATE            = 42
PADEL_JAR_PATH          = Path("PaDEL-Descriptor") / "PaDEL-Descriptor.jar"
PADEL_ZIP_URL           = "https://github.com/powhenagbo/UELPROJECT/raw/main/padel.zip"
PADEL_SH_URL            = "https://github.com/powhenagbo/UELPROJECT/raw/main/padel.sh"

# Single source of truth for activity class colours
ACTIVITY_COLORS: dict[str, str] = {
    "active":       "#FF6B6B",
    "inactive":     "#4ECDC4",
    "intermediate": "#95E77E",
}

# CPK-style atom colours for 3-D plots
ATOM_COLORS: dict[int, str] = {
    1:  "#FFFFFF",  # H
    6:  "#808080",  # C
    7:  "#3050F8",  # N
    8:  "#FF0D0D",  # O
    16: "#FFFF30",  # S
}
ATOM_COLOR_DEFAULT = "#CCCCCC"


# ── Private helpers ────────────────────────────────────────────────────────────

def _filter_valid_smiles(df: pd.DataFrame, smiles_col: str) -> pd.DataFrame:
    """Return rows whose SMILES cell is a non-empty string."""
    df = df[df[smiles_col].notna()].copy()
    df = df[df[smiles_col].apply(lambda x: isinstance(x, str) and len(x.strip()) > 0)]
    return df


def _embed_3d_molecule(mol: Chem.Mol) -> Optional[Chem.Mol]:
    """
    Add hydrogens, embed in 3-D, and minimise geometry.
    Returns the embedded molecule or None on failure.
    """
    mol_3d = Chem.AddHs(mol)
    try:
        status = AllChem.EmbedMolecule(mol_3d, randomSeed=RANDOM_STATE)
        if status != 0:
            return None
        try:
            AllChem.MMFFOptimizeMolecule(mol_3d)
        except Exception:
            AllChem.UFFOptimizeMolecule(mol_3d)
        return mol_3d
    except Exception:
        return None


def _get_atom_positions_and_colors(
    mol_3d: Chem.Mol,
) -> tuple[np.ndarray, list[str]]:
    """Extract (N×3) position array and CPK colour list from a 3-D molecule."""
    conf = mol_3d.GetConformer()
    positions, colors = [], []
    for atom in mol_3d.GetAtoms():
        pos = conf.GetAtomPosition(atom.GetIdx())
        positions.append([pos.x, pos.y, pos.z])
        colors.append(ATOM_COLORS.get(atom.GetAtomicNum(), ATOM_COLOR_DEFAULT))
    return np.array(positions), colors


def _set_equal_3d_axes(ax: plt.Axes, positions: np.ndarray) -> None:
    """Apply equal-aspect scaling to a 3-D matplotlib axes."""
    if len(positions) == 0:
        return
    ranges = positions.max(axis=0) - positions.min(axis=0)
    max_range = ranges.max() / 2.0
    if max_range == 0:
        return
    mids = (positions.max(axis=0) + positions.min(axis=0)) * 0.5
    ax.set_xlim(mids[0] - max_range, mids[0] + max_range)
    ax.set_ylim(mids[1] - max_range, mids[1] + max_range)
    ax.set_zlim(mids[2] - max_range, mids[2] + max_range)


def _sample_by_class(
    df: pd.DataFrame,
    activity_col: str,
    n_total: int,
) -> pd.DataFrame:
    """
    Stratified sample of up to *n_total* rows spread evenly across classes.
    Falls back to a simple head() when groupby fails.
    """
    try:
        classes = df[activity_col].dropna().unique().tolist()
        per_class = max(1, n_total // len(classes))
        frames = [
            df[df[activity_col] == c].sample(
                min(per_class, (df[activity_col] == c).sum()),
                random_state=RANDOM_STATE,
            )
            for c in classes
        ]
        return pd.concat(frames).drop_duplicates().head(n_total).reset_index(drop=True)
    except Exception:
        return df.head(n_total).copy()


# ── Part 1: PaDEL download ─────────────────────────────────────────────────────

def download_padel() -> None:
    """Download PaDEL-Descriptor if the JAR is not already present."""
    if PADEL_JAR_PATH.exists():
        logger.info("PaDEL-Descriptor already present.")
        return
    logger.info("Downloading PaDEL-Descriptor...")
    subprocess.run(["wget", "-q", PADEL_ZIP_URL], check=True)
    subprocess.run(["wget", "-q", PADEL_SH_URL],  check=True)
    subprocess.run(["unzip", "-q", "padel.zip"],   check=True)
    logger.info("PaDEL-Descriptor downloaded and extracted.")


# ── Part 2: Data collection ────────────────────────────────────────────────────

def run_target_search(
    disease_name: str,
) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    Query ChEMBL for targets matching *disease_name*.

    Returns
    -------
    targets_df      All candidates as a DataFrame (or None if the API returns nothing).
    selected_target ChEMBL ID of the top-scoring human single-protein target
                    (or None when no target passes the filter).
    """
    logger.info("Searching ChEMBL targets for: %s", disease_name)
    target_query = new_client.target.search(disease_name)
    targets_df = pd.DataFrame.from_dict(target_query)

    if targets_df.empty:
        logger.error("No targets found for '%s'.", disease_name)
        return None, None

    logger.info("Found %d candidate targets.", len(targets_df))
    targets_df.to_csv(f"{disease_name}_all_targets_raw.csv", index=False)

    filtered = targets_df[
        (targets_df["organism"] == "Homo sapiens")
        & (targets_df["target_type"] == "SINGLE PROTEIN")
    ].copy()

    logger.info("After filter (human single-protein): %d targets.", len(filtered))

    if filtered.empty:
        logger.warning("No targets pass the organism/type filter.")
        return targets_df, None

    top = filtered.sort_values("score", ascending=False).iloc[0]
    selected_target = top["target_chembl_id"]
    logger.info(
        "Selected target: %s | %s | score=%s",
        selected_target,
        top.get("pref_name", "N/A"),
        top.get("score", "N/A"),
    )

    filtered.to_csv(f"{disease_name}_filtered_targets.csv", index=False)
    return targets_df, selected_target


def retrieve_bioactivity(selected_target: str, disease_name: str) -> pd.DataFrame:
    """Fetch IC50 bioactivity records for *selected_target* from ChEMBL."""
    logger.info("Retrieving IC50 data for %s ...", selected_target)
    res = (
        new_client.activity
        .filter(target_chembl_id=selected_target)
        .filter(standard_type="IC50")
    )
    df = pd.DataFrame.from_dict(res)
    logger.info("Retrieved %d records.", len(df))
    df.to_csv(f"{disease_name}_01_bioactivity_data_raw.csv", index=False)
    return df


def preprocess_bioactivity(
    df: pd.DataFrame,
    disease_name: str,
) -> Optional[pd.DataFrame]:
    """
    Drop rows with missing values or duplicate SMILES, assign activity classes,
    and write the curated CSV.
    """
    if df.empty:
        logger.error("Empty DataFrame passed to preprocess_bioactivity.")
        return None

    required = {"standard_value", "canonical_smiles"}
    if not required.issubset(df.columns):
        logger.error("Required columns missing: %s", required - set(df.columns))
        return None

    df = df[df["standard_value"].notna() & df["canonical_smiles"].notna()].copy()
    df = df.drop_duplicates("canonical_smiles")
    logger.info("After cleaning: %d unique compounds.", len(df))
    df.to_csv(f"{disease_name}_02_bioactivity_data_preprocessed.csv", index=False)

    cols = [c for c in ["molecule_chembl_id", "canonical_smiles", "standard_value"] if c in df.columns]
    df = df[cols].copy()

    def _assign_class(val: object) -> str:
        try:
            v = float(val)
            if v >= INACTIVE_THRESHOLD:
                return "inactive"
            if v <= ACTIVE_THRESHOLD:
                return "active"
            return "intermediate"
        except (ValueError, TypeError):
            return "intermediate"

    df["class"] = df["standard_value"].apply(_assign_class)
    df.to_csv(f"{disease_name}_03_bioactivity_data_curated.csv", index=False)

    counts = df["class"].value_counts()
    logger.info(
        "Class distribution — active: %d | inactive: %d | intermediate: %d",
        counts.get("active", 0),
        counts.get("inactive", 0),
        counts.get("intermediate", 0),
    )
    return df


def calculate_lipinski(smiles_list: list[str]) -> pd.DataFrame:
    """
    Compute MW, LogP, NumHDonors, NumHAcceptors for each SMILES.
    Salts are stripped by keeping only the largest fragment.
    Invalid SMILES produce NaN rows.
    """
    records = []
    for smiles in smiles_list:
        largest_fragment = max(str(smiles).split("."), key=len)
        mol = Chem.MolFromSmiles(largest_fragment)
        if mol is None:
            records.append([np.nan, np.nan, np.nan, np.nan])
        else:
            records.append([
                Descriptors.MolWt(mol),
                Descriptors.MolLogP(mol),
                Lipinski.NumHDonors(mol),
                Lipinski.NumHAcceptors(mol),
            ])
    return pd.DataFrame(records, columns=["MW", "LogP", "NumHDonors", "NumHAcceptors"])


def calculate_pic50(
    df: pd.DataFrame,
    disease_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Convert IC50 (nM) to pIC50 = −log10(IC50 × 10⁻⁹).
    Returns the 3-class DataFrame and the binary (no-intermediate) DataFrame.
    """
    df = df.copy()
    df["standard_value_norm"] = df["standard_value"].apply(lambda x: min(float(x), 1e8))
    df["pIC50"] = -np.log10(df["standard_value_norm"] * 1e-9)
    df = df.drop(columns=["standard_value", "standard_value_norm"])

    df.to_csv(f"{disease_name}_04_bioactivity_data_3class_pIC50.csv", index=False)
    logger.info("3-class dataset: %d compounds.", len(df))

    df_2class = df[df["class"] != "intermediate"].copy()
    df_2class.to_csv(f"{disease_name}_05_bioactivity_data_2class_pIC50.csv", index=False)
    logger.info("2-class dataset: %d compounds.", len(df_2class))

    return df, df_2class


def run_padel_descriptors(disease_name: str) -> Optional[pd.DataFrame]:
    """
    Write a SMILES file and run PaDEL-Descriptor via the padel.sh wrapper.
    Returns the ML-ready DataFrame (descriptors + pIC50 + class), or None on failure.
    """
    csv_path = f"{disease_name}_05_bioactivity_data_2class_pIC50.csv"
    df = pd.read_csv(csv_path)
    df[["canonical_smiles", "molecule_chembl_id"]].to_csv(
        "molecule.smi", sep="\t", index=False, header=False
    )
    logger.info("Created molecule.smi (%d entries).", len(df))

    logger.info("Running PaDEL-Descriptor — this may take several minutes.")
    result = subprocess.run(["bash", "padel.sh"], capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("PaDEL-Descriptor failed:\n%s", result.stderr)
        return None

    output_path = Path("descriptors_output.csv")
    if not output_path.exists():
        logger.error("PaDEL output file not found.")
        return None

    desc = pd.read_csv(output_path).drop(columns=["Name"], errors="ignore")

    constant_cols = [c for c in desc.columns if desc[c].nunique() <= 1]
    if constant_cols:
        logger.info("Removing %d constant descriptor columns.", len(constant_cols))
        desc = desc.drop(columns=constant_cols)

    df_ml = pd.concat(
        [desc, df[["pIC50", "class"]].reset_index(drop=True)],
        axis=1,
    )
    df_ml.to_csv(f"{disease_name}_06_bioactivity_data_ml_ready.csv", index=False)
    logger.info("ML-ready dataset shape: %s", df_ml.shape)
    return df_ml


# ── Part 3: 2-D visualisations ─────────────────────────────────────────────────

def create_molecule_grid(
    df: pd.DataFrame,
    smiles_col: str,
    activity_col: str,
    pic50_col: str,
    disease_name: str,
    output_file: Optional[str] = None,
) -> list[Chem.Mol]:
    """Render a static 2-D molecular grid (PNG) sampled by class."""
    logger.info("Creating 2-D molecule grid...")
    if output_file is None:
        output_file = f"{disease_name}_molecule_grid.png"

    valid_df = _filter_valid_smiles(df, smiles_col)
    if valid_df.empty:
        logger.warning("No valid SMILES for molecule grid.")
        return []

    sampled = _sample_by_class(valid_df, activity_col, n_total=10)

    mols, legends = [], []
    for _, row in sampled.iterrows():
        mol = Chem.MolFromSmiles(row[smiles_col])
        if mol:
            mols.append(mol)
            pic50 = row.get(pic50_col, 0)
            legends.append(f"{row.get(activity_col, '')}\npIC50: {pic50:.2f}")

    if mols:
        img = Draw.MolsToGridImage(
            mols, molsPerRow=5, subImgSize=(300, 300), legends=legends, useSVG=False
        )
        img.save(output_file)
        logger.info("Molecule grid saved: %s", output_file)

    return mols


def create_class_distribution_plot(df: pd.DataFrame, disease_name: str) -> None:
    """Bar chart of active / inactive / intermediate counts."""
    logger.info("Creating class distribution plot...")
    if "class" not in df.columns:
        logger.warning("'class' column not found — skipping.")
        return

    counts = df["class"].value_counts()
    total  = counts.sum()

    _, ax = plt.subplots(figsize=(6, 5))
    bars = ax.bar(
        counts.index,
        counts.values,
        color=[ACTIVITY_COLORS.get(c, "gray") for c in counts.index],
    )
    for bar in bars:
        h = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            h,
            f"{h}\n({100 * h / total:.1f}%)",
            ha="center",
            va="bottom",
        )
    ax.set_xlabel("Bioactivity class", fontsize=12)
    ax.set_ylabel("Count", fontsize=12)
    ax.set_title("Class distribution")
    plt.tight_layout()
    plt.savefig(f"{disease_name}_class_distribution.pdf", bbox_inches="tight")
    plt.close()
    logger.info("Class distribution plot saved.")


def create_scatter_plot(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    color_col: str,
    disease_name: str,
) -> None:
    """Scatter plot of *x_col* vs *y_col*, coloured by activity class."""
    missing = {x_col, y_col, color_col} - set(df.columns)
    if missing:
        logger.warning("Scatter plot skipped — missing columns: %s", missing)
        return

    logger.info("Creating scatter plot: %s vs %s", x_col, y_col)
    _, ax = plt.subplots(figsize=(8, 6))

    for activity, color in ACTIVITY_COLORS.items():
        subset = df[df[color_col] == activity]
        if not subset.empty:
            ax.scatter(
                subset[x_col], subset[y_col],
                c=color, label=activity, alpha=0.6, s=50,
            )

    ax.set_xlabel(x_col, fontsize=12)
    ax.set_ylabel(y_col, fontsize=12)
    ax.set_title(f"{x_col} vs {y_col}")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{disease_name}_scatter_{x_col}_vs_{y_col}.pdf", bbox_inches="tight")
    plt.close()


def create_property_boxplots(df: pd.DataFrame, disease_name: str) -> None:
    """Box-plots of Lipinski descriptors and pIC50 by activity class."""
    if "class" not in df.columns:
        logger.warning("'class' column not found — skipping box plots.")
        return

    descriptors = [d for d in ["pIC50", "MW", "LogP", "NumHDonors", "NumHAcceptors"] if d in df.columns]
    if not descriptors:
        logger.warning("No descriptor columns found — skipping box plots.")
        return

    logger.info("Creating property box plots...")
    fig, axes = plt.subplots(1, len(descriptors), figsize=(5 * len(descriptors), 5))
    if len(descriptors) == 1:
        axes = [axes]

    for ax, desc in zip(axes, descriptors):
        df.boxplot(column=desc, by="class", ax=ax, grid=False)
        ax.set_title(desc)
        ax.set_xlabel("Class")
        ax.set_ylabel(desc)

    plt.suptitle("Property distribution by activity class")
    plt.tight_layout()
    plt.savefig(f"{disease_name}_property_boxplots.pdf", bbox_inches="tight")
    plt.close()
    logger.info("Property box plots saved.")


def create_correlation_heatmap(df: pd.DataFrame, disease_name: str) -> None:
    """Heatmap of descriptor correlations (top-20 by variance if many columns)."""
    numeric_cols = [c for c in df.select_dtypes(include=[np.number]).columns if c != "pIC50"]

    if len(numeric_cols) < 2:
        logger.warning("Not enough numeric columns for heatmap — skipping.")
        return

    if len(numeric_cols) > 20:
        numeric_cols = df[numeric_cols].var().nlargest(20).index.tolist()

    logger.info("Creating correlation heatmap (%d columns)...", len(numeric_cols))
    plt.figure(figsize=(12, 10))
    sns.heatmap(
        df[numeric_cols].corr(),
        annot=True, fmt=".2f", cmap="RdBu", center=0,
        square=True, linewidths=0.5,
    )
    plt.title("Descriptor correlation matrix")
    plt.tight_layout()
    plt.savefig(f"{disease_name}_correlation_heatmap.pdf", bbox_inches="tight")
    plt.close()
    logger.info("Correlation heatmap saved.")


def create_radar_chart(df: pd.DataFrame, disease_name: str) -> None:
    """Polar/radar chart comparing mean Lipinski properties across activity classes."""
    if "class" not in df.columns:
        logger.warning("'class' column not found — skipping radar chart.")
        return

    features = [f for f in ["MW", "LogP", "NumHDonors", "NumHAcceptors"] if f in df.columns]
    if len(features) < 3:
        logger.warning("Too few features for radar chart (%d found) — skipping.", len(features))
        return

    logger.info("Creating radar chart...")
    class_means = {
        activity: [df[df["class"] == activity][f].mean() for f in features]
        for activity in df["class"].unique()
    }

    min_vals = [min(v[i] for v in class_means.values()) for i in range(len(features))]
    max_vals = [max(v[i] for v in class_means.values()) for i in range(len(features))]

    angles = np.linspace(0, 2 * np.pi, len(features), endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw={"projection": "polar"})
    for activity, values in class_means.items():
        norm = [
            (v - lo) / (hi - lo) if hi > lo else 0.5
            for v, lo, hi in zip(values, min_vals, max_vals)
        ]
        norm += norm[:1]
        color = ACTIVITY_COLORS.get(activity, "gray")
        ax.plot(angles, norm, "o-", linewidth=2, label=activity, color=color)
        ax.fill(angles, norm, alpha=0.25, color=color)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(features)
    ax.set_ylim(0, 1)
    ax.set_title("Property comparison across activity classes")
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.0))
    plt.tight_layout()
    plt.savefig(f"{disease_name}_radar_chart.pdf", bbox_inches="tight")
    plt.close()
    logger.info("Radar chart saved.")


def create_mw_distribution_plot(df: pd.DataFrame, disease_name: str) -> None:
    """Overlapping MW histograms coloured by activity class."""
    if {"MW", "class"} - set(df.columns):
        logger.warning("Missing 'MW' or 'class' column — skipping MW distribution plot.")
        return

    logger.info("Creating MW distribution plot...")
    plt.figure(figsize=(10, 6))
    for activity, color in ACTIVITY_COLORS.items():
        data = df[df["class"] == activity]["MW"].dropna()
        if not data.empty:
            plt.hist(data, bins=30, alpha=0.5, label=activity, color=color, density=True)

    plt.xlabel("Molecular weight (Da)", fontsize=12)
    plt.ylabel("Density", fontsize=12)
    plt.title("MW distribution by activity class")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{disease_name}_mw_distribution.pdf", bbox_inches="tight")
    plt.close()
    logger.info("MW distribution plot saved.")


# ── Part 4: 3-D visualisations ─────────────────────────────────────────────────

def smiles_to_3d_molblock(smiles: str) -> Optional[str]:
    """
    Convert a SMILES string to a 3-D MolBlock (SDF format).
    Returns None when embedding fails or the SMILES is invalid.
    """
    if not isinstance(smiles, str) or not smiles.strip():
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    mol_3d = _embed_3d_molecule(mol)
    return Chem.MolToMolBlock(mol_3d) if mol_3d else None


def save_py3dmol_viewer_from_smiles(
    smiles: str,
    title: str,
    output_file: str,
    width: int = 900,
    height: int = 600,
) -> bool:
    """
    Write a self-contained HTML page containing an interactive py3Dmol viewer
    for the given SMILES string.  Returns True on success.
    """
    molblock = smiles_to_3d_molblock(smiles)
    if molblock is None:
        return False

    view = py3Dmol.view(width=width, height=height)
    view.addModel(molblock, "mol")
    view.setStyle({"stick": {}})
    view.addSurface(py3Dmol.VDW, {"opacity": 0.25})
    view.setBackgroundColor("white")
    view.zoomTo()

    html_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body  {{ font-family: Arial, sans-serif; margin: 24px; background: #f7f7f7; }}
    .card {{ max-width: 980px; margin: auto; background: white; padding: 20px;
             border-radius: 14px; box-shadow: 0 4px 16px rgba(0,0,0,0.08); }}
    h1   {{ margin-top: 0; font-size: 1.4rem; }}
    p    {{ color: #444; line-height: 1.4; }}
    code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 6px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <p>Interactive 3-D viewer generated from SMILES.</p>
    {view._make_html()}
  </div>
</body>
</html>"""

    Path(output_file).write_text(html_page, encoding="utf-8")
    return True


def create_interactive_3d_viewer_gallery(
    df: pd.DataFrame,
    smiles_col: str,
    activity_col: str,
    pic50_col: str,
    disease_name: str,
    n_molecules: int = 9,
    output_dir: str = "3d_visualizations",
) -> Optional[str]:
    """
    Generate individual py3Dmol HTML viewers for a stratified sample of molecules
    and write a linked index page.  Returns the path to the index file.
    """
    logger.info("Creating interactive py3Dmol viewers...")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    valid_df = _filter_valid_smiles(df, smiles_col)
    if valid_df.empty:
        logger.warning("No valid SMILES for py3Dmol viewers.")
        return None

    sampled = _sample_by_class(valid_df, activity_col, n_total=n_molecules)

    entries = []
    for idx, row in sampled.iterrows():
        smiles   = row[smiles_col]
        activity = row.get(activity_col, "unknown")
        pic50    = row.get(pic50_col, np.nan)
        label    = row.get("molecule_chembl_id", f"molecule_{idx + 1}")
        safe_label = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in str(label))

        viewer_path = os.path.join(output_dir, f"{disease_name}_viewer_{safe_label}.html")
        pic50_str   = f"{pic50:.2f}" if pd.notna(pic50) else "N/A"
        title       = f"{disease_name.title()} | {activity} | {label} | pIC50: {pic50_str}"

        if save_py3dmol_viewer_from_smiles(smiles, title, viewer_path):
            entries.append({
                "file":     os.path.basename(viewer_path),
                "title":    title,
                "smiles":   smiles,
                "activity": activity,
                "pic50":    pic50,
            })

    if not entries:
        logger.warning("No interactive viewers were created.")
        return None

    cards_html = "\n".join(
        f"""<div class="card">
  <h2>{e['title']}</h2>
  <p><strong>Activity:</strong> {e['activity']}<br>
     <strong>pIC50:</strong> {f"{e['pic50']:.2f}" if pd.notna(e['pic50']) else 'N/A'}<br>
     <strong>SMILES:</strong> <code>{e['smiles']}</code></p>
  <p><a href="{e['file']}" target="_blank">Open interactive viewer</a></p>
</div>"""
        for e in entries
    )

    index_path = os.path.join(output_dir, f"{disease_name}_interactive_3d_viewers.html")
    Path(index_path).write_text(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{disease_name.title()} Interactive 3-D Viewers</title>
  <style>
    body  {{ font-family: Arial, sans-serif; margin: 24px; background: #f7f7f7; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; }}
    .card {{ background: white; padding: 18px; border-radius: 14px;
             box-shadow: 0 4px 16px rgba(0,0,0,0.08); }}
    code  {{ background: #f0f0f0; padding: 2px 6px; border-radius: 6px;
             display: inline-block; word-break: break-all; }}
    a     {{ text-decoration: none; }}
  </style>
</head>
<body>
  <h1>{disease_name.title()} — Interactive 3-D SMILES Viewers</h1>
  <p>Click any card to inspect the molecule in an interactive py3Dmol viewer.</p>
  <div class="grid">{cards_html}</div>
</body>
</html>""",
        encoding="utf-8",
    )

    logger.info("Viewer gallery saved: %s (%d molecules)", index_path, len(entries))
    return index_path


def create_3d_molecular_plot(
    mol: Chem.Mol,
    title: str = "3-D molecular structure",
    output_file: Optional[str] = None,
) -> Optional[plt.Figure]:
    """Static matplotlib 3-D scatter plot for a single molecule."""
    if mol is None:
        return None

    mol_3d = _embed_3d_molecule(mol)
    if mol_3d is None:
        return None

    positions, colors = _get_atom_positions_and_colors(mol_3d)

    fig = plt.figure(figsize=(10, 8))
    ax  = fig.add_subplot(111, projection="3d")

    for pos, color in zip(positions, colors):
        ax.scatter(*pos, c=color, s=200, edgecolors="black", alpha=0.9)

    for bond in mol_3d.GetBonds():
        s, e = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        ax.plot(
            [positions[s, 0], positions[e, 0]],
            [positions[s, 1], positions[e, 1]],
            [positions[s, 2], positions[e, 2]],
            "k-", linewidth=2,
        )

    ax.set_title(title)
    ax.set_xlabel("X (Å)")
    ax.set_ylabel("Y (Å)")
    ax.set_zlabel("Z (Å)")
    _set_equal_3d_axes(ax, positions)

    if output_file:
        plt.savefig(output_file, dpi=150, bbox_inches="tight")
    plt.close()
    return fig


def create_3d_molecule_grid(
    df: pd.DataFrame,
    smiles_col: str,
    activity_col: str,
    pic50_col: str,
    disease_name: str,
    n_molecules: int = 9,
    output_dir: str = "3d_visualizations",
) -> Optional[str]:
    """Grid of static 3-D molecular plots, one subplot per sampled compound."""
    logger.info("Creating 3-D molecule grid...")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    valid_df = _filter_valid_smiles(df, smiles_col)
    if valid_df.empty:
        logger.warning("No valid SMILES for 3-D grid.")
        return None

    sampled = _sample_by_class(valid_df, activity_col, n_total=n_molecules)

    n_cols = 3
    n_rows = (len(sampled) + n_cols - 1) // n_cols
    fig    = plt.figure(figsize=(5 * n_cols, 5 * n_rows))

    plotted = 0
    for _, row in sampled.iterrows():
        mol = Chem.MolFromSmiles(row[smiles_col])
        if mol is None:
            continue

        mol_3d = _embed_3d_molecule(mol)
        if mol_3d is None:
            continue

        positions, colors = _get_atom_positions_and_colors(mol_3d)
        ax = fig.add_subplot(n_rows, n_cols, plotted + 1, projection="3d")

        for pos, color in zip(positions, colors):
            ax.scatter(*pos, c=color, s=100, edgecolors="black")

        for bond in mol_3d.GetBonds():
            s, e = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
            ax.plot(
                [positions[s, 0], positions[e, 0]],
                [positions[s, 1], positions[e, 1]],
                [positions[s, 2], positions[e, 2]],
                "k-", linewidth=1.5,
            )

        activity = row.get(activity_col, "unknown")
        pic50    = row.get(pic50_col, 0)
        ax.set_title(
            f"{activity}\npIC50: {pic50:.2f}",
            fontsize=10,
            color=ACTIVITY_COLORS.get(activity, "black"),
        )
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_zticks([])
        ax.grid(False)
        _set_equal_3d_axes(ax, positions)
        plotted += 1

    if plotted == 0:
        logger.warning("No valid 3-D structures generated.")
        plt.close()
        return None

    plt.suptitle("3-D molecular structures", fontsize=14)
    plt.tight_layout()
    output_file = f"{output_dir}/{disease_name}_3d_molecule_grid.pdf"
    plt.savefig(output_file, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("3-D molecule grid saved: %s", output_file)
    return output_file


def create_chemical_space_3d(
    df: pd.DataFrame,
    disease_name: str,
    output_dir: str = "3d_visualizations",
) -> Optional[str]:
    """PCA-reduced 3-D scatter plot of Morgan fingerprints coloured by activity class."""
    logger.info("Creating 3-D chemical space map...")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    if "canonical_smiles" not in df.columns:
        logger.warning("'canonical_smiles' not found — skipping chemical space map.")
        return None

    fingerprints, valid_meta = [], []
    skipped = 0

    for _, row in df.iterrows():
        smiles = row["canonical_smiles"]
        if not isinstance(smiles, str) or not smiles.strip():
            skipped += 1
            continue
        try:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                skipped += 1
                continue
            fp  = AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=512)
            arr = np.zeros((512,))
            DataStructs.ConvertToNumpyArray(fp, arr)
            fingerprints.append(arr)
            valid_meta.append({"pIC50": row.get("pIC50", 0), "class": row.get("class", "unknown")})
        except Exception:
            skipped += 1

    if skipped:
        logger.warning("Skipped %d invalid SMILES.", skipped)

    if len(fingerprints) < 10:
        logger.warning("Too few valid molecules (%d) for PCA — skipping.", len(fingerprints))
        return None

    logger.info("Running PCA on %d Morgan fingerprints.", len(fingerprints))
    X_pca = PCA(n_components=3).fit(np.array(fingerprints))
    explained = X_pca.explained_variance_ratio_
    coords    = X_pca.transform(np.array(fingerprints))

    fig = plt.figure(figsize=(12, 10))
    ax  = fig.add_subplot(111, projection="3d")

    for activity, color in ACTIVITY_COLORS.items():
        mask = np.array([d["class"] == activity for d in valid_meta])
        if mask.any():
            ax.scatter(
                coords[mask, 0], coords[mask, 1], coords[mask, 2],
                c=color, marker="o", s=50, alpha=0.6, label=activity,
            )

    ax.set_xlabel(f"PC1 ({explained[0]:.1%})")
    ax.set_ylabel(f"PC2 ({explained[1]:.1%})")
    ax.set_zlabel(f"PC3 ({explained[2]:.1%})")
    ax.set_title(f"3-D chemical space  |  total variance: {explained.sum():.1%}")
    ax.legend()
    plt.tight_layout()

    output_file = f"{output_dir}/{disease_name}_3d_chemical_space.pdf"
    plt.savefig(output_file, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("Chemical space map saved: %s", output_file)
    return output_file


def create_3d_property_space(
    df: pd.DataFrame,
    disease_name: str,
    x_prop: str = "MW",
    y_prop: str = "LogP",
    z_prop: str = "pIC50",
    output_dir: str = "3d_visualizations",
) -> Optional[str]:
    """3-D scatter of three physicochemical properties coloured by activity class."""
    logger.info("Creating 3-D property space: %s / %s / %s", x_prop, y_prop, z_prop)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    required = {x_prop, y_prop, z_prop, "class"}
    if not required.issubset(df.columns):
        logger.warning("Missing columns for 3-D property space: %s", required - set(df.columns))
        return None

    plot_df = df[[x_prop, y_prop, z_prop, "class"]].dropna()
    if plot_df.empty:
        logger.warning("No valid data points for 3-D property space.")
        return None

    fig = plt.figure(figsize=(12, 10))
    ax  = fig.add_subplot(111, projection="3d")

    for activity, color in ACTIVITY_COLORS.items():
        data = plot_df[plot_df["class"] == activity]
        if not data.empty:
            ax.scatter(
                data[x_prop], data[y_prop], data[z_prop],
                c=color, marker="o", s=50, alpha=0.6, label=activity,
            )

    ax.set_xlabel(x_prop)
    ax.set_ylabel(y_prop)
    ax.set_zlabel(z_prop)
    ax.set_title(f"3-D property space: {x_prop} · {y_prop} · {z_prop}")
    ax.legend()
    plt.tight_layout()

    output_file = f"{output_dir}/{disease_name}_3d_property_space.pdf"
    plt.savefig(output_file, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("3-D property space saved: %s", output_file)
    return output_file


def generate_all_visualizations(df_2class: pd.DataFrame, disease_name: str) -> None:
    """Run the full visualisation suite on the 2-class dataset."""
    logger.info("Generating all visualisations...")

    # 2-D plots
    create_class_distribution_plot(df_2class, disease_name)
    create_mw_distribution_plot(df_2class, disease_name)
    for x, y in [("MW", "LogP"), ("MW", "pIC50"), ("LogP", "pIC50")]:
        create_scatter_plot(df_2class, x, y, "class", disease_name)
    create_property_boxplots(df_2class, disease_name)
    create_correlation_heatmap(df_2class, disease_name)
    create_radar_chart(df_2class, disease_name)

    if "canonical_smiles" not in df_2class.columns:
        logger.info("No SMILES column — skipping molecular visualisations.")
        return

    # 2-D molecule grid
    create_molecule_grid(df_2class, "canonical_smiles", "class", "pIC50", disease_name)

    # 3-D plots
    create_3d_molecule_grid(df_2class, "canonical_smiles", "class", "pIC50", disease_name)
    create_interactive_3d_viewer_gallery(df_2class, "canonical_smiles", "class", "pIC50", disease_name)
    create_chemical_space_3d(df_2class, disease_name)
    create_3d_property_space(df_2class, disease_name)

    # Individual 3-D plots for the top active and inactive compounds
    logger.info("Creating individual 3-D plots for top molecules...")
    top_active   = df_2class[df_2class["class"] == "active"].nlargest(3, "pIC50")
    top_inactive = df_2class[df_2class["class"] == "inactive"].nsmallest(3, "pIC50")

    Path("3d_visualizations").mkdir(exist_ok=True)
    for _, row in pd.concat([top_active, top_inactive]).iterrows():
        smiles = row.get("canonical_smiles", "")
        if not isinstance(smiles, str) or not smiles:
            continue
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            continue
        create_3d_molecular_plot(
            mol,
            title=f"{row['class']} | pIC50: {row['pIC50']:.2f}",
            output_file=f"3d_visualizations/{disease_name}_3d_{row['class']}_{row.name}.pdf",
        )

    logger.info("All visualisations completed.")


# ── Part 5: Machine learning ───────────────────────────────────────────────────

def _impute_features(df_ml: pd.DataFrame) -> pd.DataFrame:
    """Mean-impute all feature columns (everything except 'pIC50' and 'class')."""
    missing_counts = df_ml.isnull().sum()
    cols_with_missing = missing_counts[missing_counts > 0]

    if cols_with_missing.empty:
        logger.info("No missing values found.")
        return df_ml

    logger.info("Imputing missing values in %d columns.", len(cols_with_missing))
    X = df_ml.drop(columns=["pIC50", "class"])
    y = df_ml[["pIC50", "class"]]

    X_imputed = SimpleImputer(strategy="mean").fit_transform(X)
    df_clean  = pd.DataFrame(X_imputed, columns=X.columns)
    return pd.concat([df_clean, y.reset_index(drop=True)], axis=1)


def build_ml_models(df_ml: pd.DataFrame, disease_name: str) -> pd.DataFrame:
    """
    Train Logistic Regression, Random Forest, Gradient Boosting, and SVM classifiers.
    Evaluates each with a held-out test set and 5-fold cross-validation.
    Saves confusion-matrix PDFs and a model-comparison CSV.

    Returns a DataFrame of results sorted by accuracy (best model first).
    """
    logger.info("Starting ML model building...")
    df_ml = _impute_features(df_ml)

    X = df_ml.drop(columns=["pIC50", "class"])
    y = df_ml["class"]

    le       = LabelEncoder()
    y_enc    = le.fit_transform(y)
    logger.info("Dataset: %s | classes: %s", X.shape, list(le.classes_))

    X_train, X_test, y_train, y_test = train_test_split(
        X, y_enc, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y_enc,
    )

    scaler         = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled  = scaler.transform(X_test)

    models = {
        "Logistic Regression": LogisticRegression(max_iter=1000, random_state=RANDOM_STATE),
        "Random Forest":       RandomForestClassifier(random_state=RANDOM_STATE, n_jobs=-1),
        "Gradient Boosting":   GradientBoostingClassifier(random_state=RANDOM_STATE),
        "SVM":                 SVC(probability=True, random_state=RANDOM_STATE),
    }

    results = []
    for name, model in models.items():
        logger.info("Training %s ...", name)
        try:
            model.fit(X_train_scaled, y_train)
            y_pred = model.predict(X_test_scaled)

            cv_scores = cross_val_score(model, X_train_scaled, y_train, cv=5, scoring="accuracy")
            results.append({
                "Model":    name,
                "Accuracy": accuracy_score(y_test, y_pred),
                "Precision": precision_score(y_test, y_pred, average="weighted", zero_division=0),
                "Recall":   recall_score(y_test, y_pred, average="weighted", zero_division=0),
                "F1-Score": f1_score(y_test, y_pred, average="weighted", zero_division=0),
                "CV Mean":  cv_scores.mean(),
                "CV Std":   cv_scores.std(),
            })
            logger.info(
                "%s — accuracy: %.4f | 5-fold CV: %.4f ± %.4f",
                name, results[-1]["Accuracy"], cv_scores.mean(), cv_scores.std(),
            )

            # Confusion matrix
            cm = confusion_matrix(y_test, y_pred)
            plt.figure(figsize=(6, 5))
            sns.heatmap(
                cm, annot=True, fmt="d", cmap="Blues",
                xticklabels=le.classes_, yticklabels=le.classes_,
            )
            plt.title(f"Confusion matrix — {name}")
            plt.ylabel("True label")
            plt.xlabel("Predicted label")
            plt.tight_layout()
            plt.savefig(f"{disease_name}_cm_{name.replace(' ', '_')}.pdf", bbox_inches="tight")
            plt.close()

        except Exception as exc:
            logger.error("Training %s failed: %s", name, exc)

    results_df = pd.DataFrame(results).sort_values("Accuracy", ascending=False)
    if not results_df.empty:
        logger.info("Model comparison:\n%s", results_df.to_string(index=False))
        results_df.to_csv(f"{disease_name}_model_comparison.csv", index=False)

    return results_df


# ── Part 6: Archive ────────────────────────────────────────────────────────────

def create_archive(disease_name: str) -> bool:
    """Collect all pipeline output files into a single ZIP archive."""
    candidates = [
        f"{disease_name}_all_targets_raw.csv",
        f"{disease_name}_filtered_targets.csv",
        f"{disease_name}_01_bioactivity_data_raw.csv",
        f"{disease_name}_02_bioactivity_data_preprocessed.csv",
        f"{disease_name}_03_bioactivity_data_curated.csv",
        f"{disease_name}_04_bioactivity_data_3class_pIC50.csv",
        f"{disease_name}_05_bioactivity_data_2class_pIC50.csv",
        f"{disease_name}_06_bioactivity_data_ml_ready.csv",
        f"{disease_name}_model_comparison.csv",
        f"{disease_name}_class_distribution.pdf",
        f"{disease_name}_mw_distribution.pdf",
        f"{disease_name}_property_boxplots.pdf",
        f"{disease_name}_correlation_heatmap.pdf",
        f"{disease_name}_radar_chart.pdf",
        f"{disease_name}_molecule_grid.png",
    ]

    for plot_pair in [("MW", "LogP"), ("MW", "pIC50"), ("LogP", "pIC50")]:
        candidates.append(f"{disease_name}_scatter_{'_vs_'.join(plot_pair)}.pdf")

    viz_dir = Path("3d_visualizations")
    if viz_dir.exists():
        candidates += [str(f) for f in viz_dir.iterdir() if f.name.startswith(disease_name)]

    candidates += [
        f for f in os.listdir(".")
        if f.startswith(f"{disease_name}_cm_") and f.endswith(".pdf")
    ]

    existing = [f for f in candidates if Path(f).exists()]
    if not existing:
        logger.warning("No output files found to archive.")
        return False

    zip_path = f"{disease_name}_drug_discovery_complete.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in existing:
            zf.write(f)
    logger.info("Archive created: %s (%d files)", zip_path, len(existing))
    return True


# ── Part 7: Main pipeline ──────────────────────────────────────────────────────

def run_pipeline(
    disease_name: str = DEFAULT_DISEASE,
    target_chembl_id: Optional[str] = None,
    do_visuals: bool = True,
    do_padel: bool = False,
    do_ml: bool = True,
    do_archive: bool = True,
) -> dict:
    """
    Execute the complete drug discovery pipeline programmatically.

    Parameters
    ----------
    disease_name     : Search term passed to ChEMBL target search.
    target_chembl_id : Override automatic target selection with a known ID.
    do_visuals       : Generate 2-D and 3-D visualisation plots.
    do_padel         : Run PaDEL fingerprint calculation before ML.
    do_ml            : Train and evaluate ML classifiers.
    do_archive       : Bundle all outputs into a ZIP archive.

    Returns
    -------
    dict with keys: disease_name, selected_target, run_id, steps, ok.
    """
    summary: dict = {"disease_name": disease_name, "steps": [], "ok": True}

    db = PipelineDBSaver() if PipelineDBSaver is not None else None
    if db and db.ready:
        logger.info("Database connection ready — results will be saved.")
    else:
        logger.info("No database available — results saved to CSV only.")

    # ── Step 1: Target search ──────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 1 — TARGET SEARCH")
    targets_df, selected_target = run_target_search(disease_name)

    if selected_target is None and target_chembl_id:
        selected_target = target_chembl_id
        logger.info("Using caller-supplied target: %s", selected_target)

    if selected_target is None:
        logger.error("No target selected — aborting pipeline.")
        summary["ok"] = False
        return summary

    summary["selected_target"] = selected_target

    run_id = None
    if db and db.ready:
        run_id = db.start_run(
            disease_name=disease_name,
            selected_target=selected_target,
            run_dir=str(Path(".").resolve()),
        )
        db.save_targets(run_id, targets_df)
    summary["run_id"] = run_id

    # ── Step 2: Bioactivity retrieval ──────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 2 — BIOACTIVITY RETRIEVAL")
    df_raw = retrieve_bioactivity(selected_target, disease_name)
    if df_raw.empty:
        logger.error("No bioactivity data found — aborting.")
        summary["ok"] = False
        if db and db.ready:
            db.fail_run(run_id, "fetch_bioactivity")
        return summary

    if db and db.ready:
        db.save_bioactivity(run_id, df_raw)

    # ── Step 3: Preprocessing ──────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 3 — PREPROCESSING")
    df_curated = preprocess_bioactivity(df_raw, disease_name)
    if df_curated is None:
        summary["ok"] = False
        if db and db.ready:
            db.fail_run(run_id, "preprocess_bioactivity")
        return summary

    # ── Step 4: Descriptors + pIC50 ───────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 4 — DESCRIPTOR CALCULATION")
    df_lipinski = calculate_lipinski(df_curated["canonical_smiles"].tolist())
    df_combined = pd.concat([df_curated.reset_index(drop=True), df_lipinski], axis=1)
    df_3class, df_2class = calculate_pic50(df_combined, disease_name)

    # Attach SMILES back onto the 2-class frame
    smiles_map = dict(zip(df_curated["molecule_chembl_id"], df_curated["canonical_smiles"]))
    df_2class["canonical_smiles"] = df_2class["molecule_chembl_id"].map(smiles_map)

    if db and db.ready:
        db.save_compounds(run_id, df_3class)

    # ── Step 5: Visualisations ─────────────────────────────────────────────────
    if do_visuals:
        logger.info("=" * 60)
        logger.info("STEP 5 — VISUALISATIONS")
        generate_all_visualizations(df_2class, disease_name)

    # ── Step 6: PaDEL + ML ────────────────────────────────────────────────────
    results_df = None
    if do_padel and do_ml:
        logger.info("=" * 60)
        logger.info("STEP 6 — PADEL DESCRIPTORS")
        download_padel()
        df_ml = run_padel_descriptors(disease_name)

        if df_ml is not None:
            logger.info("=" * 60)
            logger.info("STEP 7 — MACHINE LEARNING")
            results_df = build_ml_models(df_ml, disease_name)
            if db and db.ready and results_df is not None and not results_df.empty:
                db.save_ml_results(run_id, results_df)

    # ── Step 8: Archive ────────────────────────────────────────────────────────
    if do_archive:
        logger.info("=" * 60)
        logger.info("STEP 8 — ARCHIVE")
        create_archive(disease_name)

    # ── Finalise ───────────────────────────────────────────────────────────────
    if db and db.ready:
        db.finish_run(
            run_id,
            status="completed",
            selected_target=selected_target,
        )

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETED — %s", disease_name)
    logger.info("Output files prefixed: %s_", disease_name)
    if run_id:
        logger.info("Database run_id=%s saved to drugdb.", run_id)

    summary["results"] = results_df
    return summary


# ── CLI entry-point ────────────────────────────────────────────────────────────

def main() -> None:
    """Interactive CLI wrapper around run_pipeline()."""
    print("\nEnter disease name (e.g. diabetes, cancer, alzheimer):")
    disease_name = input("> ").strip() or DEFAULT_DISEASE

    print("\nEnter a target ChEMBL ID to override auto-selection (or press Enter to skip):")
    target_override = input("> ").strip() or None

    print("\nRun PaDEL descriptor calculation? (y/N):")
    do_padel = input("> ").strip().lower() == "y"

    run_pipeline(
        disease_name=disease_name,
        target_chembl_id=target_override,
        do_padel=do_padel,
    )


if __name__ == "__main__":
    main()
