"""
seed_rag.py
-----------
Run this after every pipeline run to populate the pgvector RAG store.

    python3 seed_rag.py                    # seeds all available runs
    python3 seed_rag.py --disease malaria  # seeds one specific disease
    python3 seed_rag.py --file paper.pdf   # ingest a PDF paper
    python3 seed_rag.py --file notes.txt   # ingest a text file
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        print("pypdf not installed — run: pip install pypdf")
        sys.exit(1)


def read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return read_pdf(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def ingest(title: str, text: str, source: str) -> int:
    """Ingest text into pgvector store. Returns chunk count."""
    from document_rag import ingest_text
    text = text.strip()
    if not text:
        print(f"  [skip] '{title}' — empty content")
        return 0
    n = ingest_text(title=title, text=text, source=source)
    print(f"  [ok]   '{title}' — {n} chunks ingested")
    return n


# ── seed from pipeline run outputs ───────────────────────────────────────────

def seed_from_runs(disease: str | None = None) -> None:
    runs_dir = Path(__file__).resolve().parent / "runs"
    if not runs_dir.exists():
        print("No runs/ folder found. Run the pipeline first.")
        return

    diseases = (
        [runs_dir / disease]
        if disease else
        [d for d in runs_dir.iterdir() if d.is_dir()]
    )

    for disease_dir in diseases:
        name = disease_dir.name
        print(f"\nSeeding from run: {name}")

        # ML model comparison
        ml_csv = disease_dir / f"{name}_model_comparison.csv"
        if ml_csv.exists():
            ingest(
                title=f"{name} ML model comparison",
                text=ml_csv.read_text(),
                source="pipeline_output",
            )

        # 3-class bioactivity summary (curated)
        curated = disease_dir / f"{name}_03_bioactivity_data_curated.csv"
        if curated.exists():
            # summarize rather than ingest raw CSV (can be large)
            import pandas as pd
            df = pd.read_csv(curated)
            n_total = len(df)
            n_active = int((df["class"] == "active").sum())
            n_inactive = int((df["class"] == "inactive").sum())
            n_inter = int((df["class"] == "intermediate").sum())
            summary = (
                f"Bioactivity summary for {name}:\n"
                f"Total compounds: {n_total}\n"
                f"Active (IC50 <= 1000 nM): {n_active}\n"
                f"Inactive (IC50 >= 10000 nM): {n_inactive}\n"
                f"Intermediate: {n_inter}\n"
            )
            ingest(
                title=f"{name} bioactivity summary",
                text=summary,
                source="pipeline_output",
            )

        # filtered targets
        targets_csv = disease_dir / f"{name}_filtered_targets.csv"
        if targets_csv.exists():
            import pandas as pd
            df = pd.read_csv(targets_csv)
            lines = [f"Targets found for {name}:"]
            for _, r in df.head(10).iterrows():
                lines.append(
                    f"  {r.get('target_chembl_id','')} — "
                    f"{r.get('pref_name','')} "
                    f"(score: {r.get('score','')})"
                )
            ingest(
                title=f"{name} ChEMBL targets",
                text="\n".join(lines),
                source="pipeline_output",
            )


# ── seed background knowledge ─────────────────────────────────────────────────

def seed_background_knowledge() -> None:
    print("\nSeeding background knowledge...")

    ingest(
        title="Drug discovery workflow overview",
        text="""
This drug discovery system targets infectious disease proteins using ChEMBL IC50 bioactivity data.

Compound classification:
- Active: IC50 <= 1000 nM (strong inhibitor)
- Intermediate: IC50 between 1000 and 10000 nM
- Inactive: IC50 >= 10000 nM (weak or no inhibition)

pIC50 = -log10(IC50 in molar units). Higher pIC50 = stronger compound.
A pIC50 above 6.0 is generally considered active for drug candidates.

Lipinski rule of five (drug-likeness):
- Molecular weight (MW) < 500 Da
- LogP (lipophilicity) < 5
- Hydrogen bond donors (HBD) <= 5
- Hydrogen bond acceptors (HBA) <= 10
Compounds violating more than one rule are unlikely to be orally bioavailable.

Targets are filtered to Homo sapiens SINGLE PROTEIN type.
The highest-scoring target by ChEMBL relevance score is selected automatically.
        """,
        source="background_knowledge",
    )

    ingest(
        title="ML models used in pipeline",
        text="""
The pipeline trains four classifiers on Lipinski descriptors + pIC50:

1. Random Forest — ensemble of decision trees, handles non-linear relationships well.
   Typically the best-performing model in this pipeline.

2. Support Vector Machine (SVM) — finds optimal hyperplane separating active/inactive.
   Works well with small datasets.

3. Logistic Regression — linear classifier, fast and interpretable.
   Good baseline comparison.

4. Gradient Boosting — sequential ensemble, corrects errors of previous trees.
   Can overfit on small datasets.

Evaluation metrics:
- Accuracy: fraction of correct predictions
- Precision: true positives / (true positives + false positives)
- Recall: true positives / (true positives + false negatives)
- F1-Score: harmonic mean of precision and recall
- CV Mean: 5-fold cross-validation mean accuracy
- CV Std: standard deviation of cross-validation scores (lower is more stable)

The model with highest accuracy on the test set is marked as BEST.
        """,
        source="background_knowledge",
    )

    ingest(
        title="SMILES and 3D structure explanation",
        text="""
SMILES (Simplified Molecular Input Line Entry System) is a text notation
for representing chemical structures.

Examples:
- Aspirin: CC(=O)Oc1ccccc1C(=O)O
- Caffeine: Cn1cnc2c1c(=O)n(c(=O)n2C)C
- Ibuprofen: CC(C)Cc1ccc(cc1)C(C)C(=O)O

3D coordinates are generated from SMILES using the ETKDG algorithm in RDKit.
The 3D structure is rendered in the browser using 3Dmol.js with WebGL.

Visualization styles:
- Stick: bonds as cylinders, atoms as small spheres
- Ball and stick: larger atom spheres
- Sphere: CPK space-filling model
- Surface: van der Waals surface (shows molecular shape)
- Wire: bond lines only

Color schemes:
- Element (CPK): C=gray, O=red, N=blue, H=white, S=yellow
- Spectrum: rainbow by atom serial number
- Chain: by chain identifier
        """,
        source="background_knowledge",
    )


# ── seed from external file ───────────────────────────────────────────────────

def seed_from_file(filepath: str, title: str | None, source: str) -> None:
    path = Path(filepath)
    if not path.exists():
        print(f"File not found: {filepath}")
        sys.exit(1)
    text = read_file(path)
    doc_title = title or path.stem.replace("_", " ").replace("-", " ")
    print(f"\nIngesting file: {path.name}")
    ingest(title=doc_title, text=text, source=source)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the pgvector RAG store with pipeline outputs and documents."
    )
    parser.add_argument("--disease", help="Seed a specific disease run only (e.g. diphtheria)")
    parser.add_argument("--file",    help="Path to a .txt or .pdf file to ingest")
    parser.add_argument("--title",   help="Title for the ingested file (optional)")
    parser.add_argument("--source",  default="manual", help="Source label (default: manual)")
    parser.add_argument("--no-background", action="store_true",
                        help="Skip background knowledge seeding")
    args = parser.parse_args()

    total = 0

    if args.file:
        seed_from_file(args.file, args.title, args.source)
    else:
        seed_from_runs(args.disease)
        if not args.no_background:
            seed_background_knowledge()

    print("\nRAG store seeding complete.")
    print("Test with: python3 -c \"from document_rag import retrieve_context; print(retrieve_context('ML results diphtheria', k=2))\"")


if __name__ == "__main__":
    main()
