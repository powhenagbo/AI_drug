"""
pipeline_db_saver.py
────────────────────
Lightweight database writer for drug_discovery_advanced_py3dmol.py.

Drop this file in the same folder as your pipeline script.
It connects to the same PostgreSQL database your API uses and saves
targets, bioactivity, compounds, and ML results after each pipeline step.

Usage (already wired into the patched main() at the bottom of this file):
    from pipeline_db_saver import PipelineDBSaver
    db = PipelineDBSaver()
    run_id = db.start_run("diphtheria", selected_target="CHEMBL2366517")
    db.save_targets(run_id, targets_df)
    db.save_bioactivity(run_id, bioactivity_df)
    db.save_compounds(run_id, compounds_df)
    db.save_ml_results(run_id, results_df)
    db.finish_run(run_id, status="completed")
"""
from __future__ import annotations

import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from dotenv import load_dotenv

# Load .env from the same directory as this script
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

logger = logging.getLogger(__name__)

# ── DB driver ─────────────────────────────────────────────────────────────────
try:
    import psycopg
    _DRIVER = "psycopg"
except ImportError:
    psycopg = None
    _DRIVER = None

if _DRIVER is None:
    try:
        import psycopg2
        _DRIVER = "psycopg2"
    except ImportError:
        psycopg2 = None


class PipelineDBSaver:
    """
    Saves pipeline results directly to the drugdb PostgreSQL database.
    If the DB is unavailable it degrades silently so the pipeline still runs.
    """

    def __init__(self) -> None:
        self.db_config: Dict[str, Any] = {
            "dbname":   os.getenv("PGDATABASE", "drugdb"),
            "user":     os.getenv("PGUSER", os.getenv("USER", "postgres")),
            "password": os.getenv("PGPASSWORD", ""),
            "host":     os.getenv("PGHOST", "localhost"),
            "port":     int(os.getenv("PGPORT", "5432")),
        }
        self.ready = _DRIVER is not None
        if not self.ready:
            logger.warning(
                "No PostgreSQL driver found — pipeline will run without DB saving.\n"
                "Install with: pip install psycopg[binary]  or  pip install psycopg2-binary"
            )
        else:
            logger.info("PipelineDBSaver ready (driver: %s)", _DRIVER)

    # ── Connection ─────────────────────────────────────────────────────────────
    def _connect(self):
        if _DRIVER == "psycopg":
            return psycopg.connect(**self.db_config)
        return psycopg2.connect(**self.db_config)

    def _exec(self, sql: str, params: tuple = (), fetch_one: bool = False):
        """Run a single statement, return first row if fetch_one=True."""
        if not self.ready:
            return None
        try:
            conn = self._connect()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchone() if fetch_one else None
        except Exception as exc:
            logger.error("DB error: %s", exc)
            return None

    def _exec_many(self, sql: str, rows: list) -> None:
        """Insert multiple rows at once."""
        if not self.ready or not rows:
            return
        try:
            conn = self._connect()
            with conn:
                with conn.cursor() as cur:
                    cur.executemany(sql, rows)
        except Exception as exc:
            logger.error("DB batch insert error: %s", exc)

    # ── Run lifecycle ──────────────────────────────────────────────────────────
    def start_run(
        self,
        disease_name: str,
        selected_target: Optional[str] = None,
        run_dir: Optional[str] = None,
    ) -> Optional[int]:
        """
        Insert a new run record and return the run_id.
        Call this BEFORE any pipeline steps.
        """
        sql = """
            INSERT INTO runs (disease_name, selected_target, status, run_dir, metadata)
            VALUES (%s, %s, 'running', %s, %s)
            RETURNING id
        """
        row = self._exec(
            sql,
            (disease_name, selected_target, run_dir or str(Path.cwd()),
             json.dumps({"source": "pipeline_direct"})),
            fetch_one=True,
        )
        run_id = row[0] if row else None
        if run_id:
            print(f"📦 DB: run started (run_id={run_id}) for '{disease_name}'")
        return run_id

    def finish_run(
        self,
        run_id: Optional[int],
        status: str = "completed",
        selected_target: Optional[str] = None,
        disease_name: Optional[str] = None,
    ) -> None:
        """Update run status to completed or failed, then auto-seed RAG."""
        if run_id is None:
            return
        parts = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
        params: list = [status]
        if selected_target:
            parts.append("selected_target = %s")
            params.append(selected_target)
        params.append(run_id)
        self._exec(
            f"UPDATE runs SET {', '.join(parts)} WHERE id = %s",
            tuple(params),
        )
        print(f"📦 DB: run_id={run_id} marked as '{status}'")

        if status == "completed" and disease_name:
            self._seed_rag(disease_name)

    def _seed_rag(self, disease_name: str) -> None:
        """Seed the pgvector RAG store with this run's pipeline outputs."""
        try:
            from document_rag import ingest_text
            import pandas as pd

            run_dir = Path.cwd()

            ml_csv = run_dir / f"{disease_name}_model_comparison.csv"
            if ml_csv.exists():
                ingest_text(
                    title=f"{disease_name} ML model comparison",
                    text=ml_csv.read_text(),
                    source="pipeline_output",
                )
                print(f"📦 RAG: ingested ML results for '{disease_name}'")

            curated = run_dir / f"{disease_name}_03_bioactivity_data_curated.csv"
            if curated.exists():
                df = pd.read_csv(curated)
                summary = (
                    f"Bioactivity summary for {disease_name}:\n"
                    f"Total compounds: {len(df)}\n"
                    f"Active (IC50 <= 1000 nM): {int((df['class']=='active').sum())}\n"
                    f"Inactive (IC50 >= 10000 nM): {int((df['class']=='inactive').sum())}\n"
                    f"Intermediate: {int((df['class']=='intermediate').sum())}\n"
                )
                ingest_text(
                    title=f"{disease_name} bioactivity summary",
                    text=summary,
                    source="pipeline_output",
                )
                print(f"📦 RAG: ingested bioactivity summary for '{disease_name}'")

            targets_csv = run_dir / f"{disease_name}_filtered_targets.csv"
            if targets_csv.exists():
                df = pd.read_csv(targets_csv)
                lines = [f"Targets found for {disease_name}:"]
                for _, r in df.head(10).iterrows():
                    lines.append(
                        f"  {r.get('target_chembl_id','')} — "
                        f"{r.get('pref_name','')} "
                        f"(score: {r.get('score','')})"
                    )
                ingest_text(
                    title=f"{disease_name} ChEMBL targets",
                    text="\n".join(lines),
                    source="pipeline_output",
                )
                print(f"📦 RAG: ingested targets for '{disease_name}'")

        except Exception as exc:
            logger.warning("RAG seeding failed (non-critical): %s", exc)

    def fail_run(self, run_id: Optional[int], step: str) -> None:
        if run_id is None:
            return
        self._exec(
            "UPDATE runs SET status='failed', metadata = metadata || %s::jsonb, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (json.dumps({"failed_step": step}), run_id),
        )

    # ── Save targets ───────────────────────────────────────────────────────────
    def save_targets(self, run_id: Optional[int], df: pd.DataFrame) -> None:
        """
        Save targets dataframe to the targets table.
        Expects columns: target_chembl_id, pref_name, organism, target_type, score
        """
        if run_id is None or df is None or df.empty:
            return

        def _f(v):
            try: return float(v)
            except: return None

        rows = []
        for _, r in df.iterrows():
            rows.append((
                run_id,
                str(r.get("pref_name", "")),
                str(r.get("target_chembl_id", "")),
                str(r.get("organism", "")),
                str(r.get("target_type", "")),
                _f(r.get("score")),
            ))

        self._exec_many(
            """
            INSERT INTO targets (run_id, pref_name, target_chembl_id, organism, target_type, score)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        print(f"📦 DB: saved {len(rows)} targets for run_id={run_id}")

    # ── Save bioactivity ───────────────────────────────────────────────────────
    def save_bioactivity(self, run_id: Optional[int], df: pd.DataFrame) -> None:
        """
        Save raw bioactivity records.
        Expects columns: molecule_chembl_id, canonical_smiles, standard_value,
                         standard_type, standard_units
        """
        if run_id is None or df is None or df.empty:
            return

        def _f(v):
            try: return float(v)
            except: return None

        rows = []
        for _, r in df.iterrows():
            rows.append((
                run_id,
                str(r.get("molecule_chembl_id", "")),
                str(r.get("canonical_smiles", "")),
                _f(r.get("standard_value")),
                str(r.get("standard_type", "IC50")),
                str(r.get("standard_units", "nM")),
            ))

        self._exec_many(
            """
            INSERT INTO bioactivity
                (run_id, molecule_chembl_id, canonical_smiles,
                 standard_value, standard_type, standard_units)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        print(f"📦 DB: saved {len(rows)} bioactivity records for run_id={run_id}")

    # ── Save compounds (with Lipinski + pIC50) ─────────────────────────────────
    def save_compounds(self, run_id: Optional[int], df: pd.DataFrame) -> None:
        """
        Save curated compounds with descriptors.
        Expects columns: molecule_chembl_id, canonical_smiles (or smiles),
                         MW, LogP, NumHDonors, NumHAcceptors, pIC50, class
        """
        if run_id is None or df is None or df.empty:
            return

        def _f(v):
            try: return float(v)
            except: return None

        def _i(v):
            try: return int(v)
            except: return None

        smiles_col = "canonical_smiles" if "canonical_smiles" in df.columns else "smiles"
        rows = []
        for _, r in df.iterrows():
            rows.append((
                run_id,
                str(r.get("molecule_chembl_id", "")),
                str(r.get(smiles_col, "")),
                _f(r.get("MW")),
                _f(r.get("LogP")),
                _i(r.get("NumHDonors")),
                _i(r.get("NumHAcceptors")),
                _f(r.get("pIC50")),
                str(r.get("class", "")),
            ))

        self._exec_many(
            """
            INSERT INTO compounds
                (run_id, molecule_chembl_id, smiles, mw, logp,
                 numhdonors, numhacceptors, pic50, class)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        print(f"📦 DB: saved {len(rows)} compounds for run_id={run_id}")

    # ── Save ML results ────────────────────────────────────────────────────────
    def save_ml_results(self, run_id: Optional[int], results_df: pd.DataFrame) -> None:
        if run_id is None or results_df is None or results_df.empty:
            return

        # Add missing columns if they don't exist yet
        for col in [
            "ALTER TABLE ml_results ADD COLUMN IF NOT EXISTS precision_score DOUBLE PRECISION",
            "ALTER TABLE ml_results ADD COLUMN IF NOT EXISTS recall_score DOUBLE PRECISION",
            "ALTER TABLE ml_results ADD COLUMN IF NOT EXISTS f1_score DOUBLE PRECISION",
            "ALTER TABLE ml_results ADD COLUMN IF NOT EXISTS cv_mean DOUBLE PRECISION",
            "ALTER TABLE ml_results ADD COLUMN IF NOT EXISTS cv_std DOUBLE PRECISION",
        ]:
            self._exec(col)

        def _f(v):
            try: return float(v)
            except: return None

        rows = []
        for i, (_, r) in enumerate(results_df.iterrows()):
            rows.append((
                run_id,
                str(r.get("Model", "")),
                _f(r.get("Accuracy")),
                _f(r.get("Precision")),
                _f(r.get("Recall")),
                _f(r.get("F1-Score")),
                _f(r.get("CV Mean")),
                _f(r.get("CV Std")),
                i == 0,
            ))

        self._exec_many(
            """
            INSERT INTO ml_results
                (run_id, model_name, accuracy, precision_score, recall_score,
                 f1_score, cv_mean, cv_std, best)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        print(f"📦 DB: saved {len(rows)} ML results for run_id={run_id}")
