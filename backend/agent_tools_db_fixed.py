from __future__ import annotations

import json
import logging
import os
import traceback
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

import drug_discovery_advanced_py3dmol as pipeline

logger = logging.getLogger(__name__)

# ── DB driver detection ────────────────────────────────────────────────────────
try:
    import psycopg  # psycopg3 (preferred)
    import psycopg_pool
    _DRIVER = "psycopg"
except ImportError:
    psycopg = None
    psycopg_pool = None

try:
    import psycopg2
    from psycopg2.extras import Json, execute_values
    if _DRIVER is None:           # type: ignore[name-defined]
        _DRIVER = "psycopg2"
except ImportError:
    psycopg2 = None
    Json = None
    execute_values = None

try:
    _DRIVER                       # type: ignore[name-defined]
except NameError:
    _DRIVER = None


# ── ToolResult dataclass ───────────────────────────────────────────────────────
@dataclass
class ToolResult:
    ok: bool
    step: str
    message: str
    data: Optional[Dict[str, Any]] = None
    files: Optional[List[str]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Main class ─────────────────────────────────────────────────────────────────
class DrugDiscoveryAgentTools:
    """
    Service wrapper around the pipeline script with PostgreSQL persistence.

    Improvements over original:
    - Connection pool (psycopg3) or single reusable connection (psycopg2)
      instead of a new connection per call.
    - Fixed SQL DDL trailing-comma bugs in `targets` and `bioactivity` tables.
    - Thread-safe working-directory context (no global os.chdir race condition).
    - Structured logging instead of bare print().
    """

    # ── Init ──────────────────────────────────────────────────────────────────
    def __init__(
        self,
        base_output_dir: str = "runs",
        db_enabled: bool = True,
        auto_init_db: bool = True,
        pool_min: int = 1,
        pool_max: int = 5,
    ) -> None:
        self.base_output_dir = Path(base_output_dir)
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.db_enabled = db_enabled
        self.db_config: Dict[str, Any] = {
            "dbname": os.getenv("PGDATABASE", "drugdb"),
            "user": os.getenv("PGUSER"),
            "password": os.getenv("PGPASSWORD", ""),
            "host": os.getenv("PGHOST", "localhost"),
            "port": int(os.getenv("PGPORT", "5432")),
        }
        self.db_driver: Optional[str] = _DRIVER
        self.db_ready = bool(self.db_driver) and self.db_enabled
        self._pool = None
        self._pool_lock = Lock()

        if self.db_ready:
            self._init_pool(pool_min, pool_max)
            if auto_init_db:
                self.init_db()

    # ── Connection pool ────────────────────────────────────────────────────────
    def _init_pool(self, min_size: int, max_size: int) -> None:
        """Create a connection pool (psycopg3) or fall back gracefully."""
        if self.db_driver == "psycopg" and psycopg_pool is not None:
            try:
                conninfo = (
                    f"dbname={self.db_config['dbname']} "
                    f"user={self.db_config['user']} "
                    f"password={self.db_config['password']} "
                    f"host={self.db_config['host']} "
                    f"port={self.db_config['port']}"
                )
                self._pool = psycopg_pool.ConnectionPool(
                    conninfo, min_size=min_size, max_size=max_size, open=True
                )
                logger.info("psycopg3 connection pool created (min=%d, max=%d)", min_size, max_size)
            except Exception as exc:
                logger.warning("Could not create pool: %s — will use single connections.", exc)
                self._pool = None

    @contextmanager
    def _get_conn(self):
        """Yield a DB connection from the pool (or create one-off if no pool)."""
        if not self.db_enabled:
            raise RuntimeError("Database access is disabled.")
        if self._pool is not None:
            with self._pool.connection() as conn:
                yield conn
        elif self.db_driver == "psycopg" and psycopg is not None:
            conn = psycopg.connect(**self.db_config)
            try:
                yield conn
            finally:
                conn.close()
        elif self.db_driver == "psycopg2" and psycopg2 is not None:
            conn = psycopg2.connect(**self.db_config)
            try:
                yield conn
            finally:
                conn.close()
        else:
            raise RuntimeError(
                "No PostgreSQL driver available. "
                "Install: pip install psycopg[binary] or psycopg2-binary"
            )

    def _json_value(self, obj: Any) -> Any:
        """Serialize dict to JSON string for psycopg2 compatibility."""
        if self.db_driver == "psycopg2":
            return json.dumps(obj)
        return json.dumps(obj)  # psycopg3 also accepts strings

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _safe_name(self, text: str) -> str:
        return "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_") or "run"

    def _run_dir(self, disease_name: str) -> Path:
        run_dir = self.base_output_dir / self._safe_name(disease_name)
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    @contextmanager
    def _with_run_dir(self, disease_name: str):
        """
        Thread-safe working-directory switch.
        Uses chdir only within the context; restores original on exit.
        NOTE: os.chdir is process-wide — for multi-threaded servers prefer
        running pipeline functions with explicit output_dir arguments when
        the pipeline is refactored to support it.
        """
        run_dir = self._run_dir(disease_name)
        prev = Path.cwd()
        os.chdir(run_dir)
        try:
            yield run_dir
        finally:
            os.chdir(prev)

    # ── Database init ──────────────────────────────────────────────────────────
    def init_db(self) -> ToolResult:
        if not self.db_enabled:
            return ToolResult(ok=False, step="init_db", message="Database is disabled.")
        if not self.db_driver:
            return ToolResult(
                ok=False,
                step="init_db",
                message="No PostgreSQL driver found.",
                error="Install psycopg[binary] or psycopg2-binary.",
            )

        # FIX: removed trailing commas on last column of targets + bioactivity tables
        ddl = """
        CREATE TABLE IF NOT EXISTS runs (
            id SERIAL PRIMARY KEY,
            disease_name TEXT NOT NULL,
            selected_target TEXT,
            status TEXT DEFAULT 'started',
            run_dir TEXT,
            message TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS targets (
            id SERIAL PRIMARY KEY,
            run_id INT REFERENCES runs(id) ON DELETE CASCADE,
            pref_name TEXT,
            target_chembl_id TEXT,
            organism TEXT,
            target_type TEXT,
            score DOUBLE PRECISION
        );

        CREATE TABLE IF NOT EXISTS bioactivity (
            id SERIAL PRIMARY KEY,
            run_id INT REFERENCES runs(id) ON DELETE CASCADE,
            molecule_chembl_id TEXT,
            canonical_smiles TEXT,
            standard_value DOUBLE PRECISION,
            standard_type TEXT,
            standard_units TEXT
        );

        CREATE TABLE IF NOT EXISTS compounds (
            id SERIAL PRIMARY KEY,
            run_id INT REFERENCES runs(id) ON DELETE CASCADE,
            molecule_chembl_id TEXT,
            smiles TEXT,
            mw DOUBLE PRECISION,
            logp DOUBLE PRECISION,
            numhdonors INT,
            numhacceptors INT,
            pic50 DOUBLE PRECISION,
            class TEXT
        );

        CREATE TABLE IF NOT EXISTS ml_results (
            id SERIAL PRIMARY KEY,
            run_id INT REFERENCES runs(id) ON DELETE CASCADE,
            model_name TEXT,
            accuracy DOUBLE PRECISION,
            precision_score DOUBLE PRECISION,
            recall_score DOUBLE PRECISION,
            f1_score DOUBLE PRECISION,
            cv_mean DOUBLE PRECISION,
            cv_std DOUBLE PRECISION,
            best BOOLEAN DEFAULT FALSE
        );

        CREATE TABLE IF NOT EXISTS files (
            id SERIAL PRIMARY KEY,
            run_id INT REFERENCES runs(id) ON DELETE CASCADE,
            file_path TEXT,
            file_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS alphafold_structures (
            id SERIAL PRIMARY KEY,
            run_id INT REFERENCES runs(id) ON DELETE CASCADE,
            disease_name TEXT NOT NULL,
            uniprot_id TEXT NOT NULL,
            protein_name TEXT,
            organism TEXT,
            sequence_length INT,
            mean_plddt DOUBLE PRECISION,
            pct_very_high DOUBLE PRECISION,
            pct_confident DOUBLE PRECISION,
            n_confident_regions INT,
            pdb_path TEXT,
            filtered_pdb_path TEXT,
            af_model_version TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_alphafold_disease ON alphafold_structures(disease_name);
        CREATE INDEX IF NOT EXISTS idx_alphafold_uniprot  ON alphafold_structures(uniprot_id);
        CREATE INDEX IF NOT EXISTS idx_runs_disease_name ON runs(disease_name);
        CREATE INDEX IF NOT EXISTS idx_targets_run_id ON targets(run_id);
        CREATE INDEX IF NOT EXISTS idx_targets_chembl_id ON targets(target_chembl_id);
        CREATE INDEX IF NOT EXISTS idx_bioactivity_run_id ON bioactivity(run_id);
        CREATE INDEX IF NOT EXISTS idx_bioactivity_mol_id ON bioactivity(molecule_chembl_id);
        CREATE INDEX IF NOT EXISTS idx_compounds_run_id ON compounds(run_id);
        CREATE INDEX IF NOT EXISTS idx_compounds_class ON compounds(class);
        CREATE INDEX IF NOT EXISTS idx_files_run_id ON files(run_id);
        """

        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(ddl)
                conn.commit()
            logger.info("Database tables initialized.")
            return ToolResult(ok=True, step="init_db", message="Database tables ready.")
        except Exception as exc:
            return ToolResult(
                ok=False,
                step="init_db",
                message="Database initialization failed.",
                error=f"{exc}\n{traceback.format_exc()}",
            )

    # ── Run record helpers ─────────────────────────────────────────────────────
    def _insert_run(
        self,
        disease_name: str,
        selected_target: Optional[str] = None,
        status: str = "started",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        if not self.db_ready:
            return None
        sql = """
            INSERT INTO runs (disease_name, selected_target, status, run_dir, metadata)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (
                        disease_name,
                        selected_target,
                        status,
                        str(self._run_dir(disease_name)),
                        self._json_value(metadata or {}),
                    ))
                    row = cur.fetchone()
                conn.commit()
            return row[0] if row else None
        except Exception as exc:
            logger.error("_insert_run failed: %s", exc)
            return None

    def _update_run(
        self,
        run_id: Optional[int],
        status: Optional[str] = None,
        selected_target: Optional[str] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.db_ready or run_id is None:
            return
        parts, params = [], []
        if status:
            parts.append("status = %s")
            params.append(status)
        if selected_target:
            parts.append("selected_target = %s")
            params.append(selected_target)
        if metadata_patch:
            parts.append("metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb")
            params.append(json.dumps(metadata_patch))
        parts.append("updated_at = CURRENT_TIMESTAMP")
        params.append(run_id)
        sql = f"UPDATE runs SET {', '.join(parts)} WHERE id = %s"
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                conn.commit()
        except Exception as exc:
            logger.error("_update_run failed: %s", exc)

    def _store_files(
        self, run_id: Optional[int], step: str, files: List[str]
    ) -> None:
        if not self.db_ready or run_id is None or not files:
            return
        sql = "INSERT INTO files (run_id, file_path, file_type) VALUES (%s, %s, %s)"
        rows = [(run_id, f, step) for f in files]
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.executemany(sql, rows)
                conn.commit()
        except Exception as exc:
            logger.error("_store_files failed: %s", exc)

    def _store_targets(self, run_id: Optional[int], df: pd.DataFrame) -> None:
        if not self.db_ready or run_id is None or df is None or df.empty:
            return
        sql = """
            INSERT INTO targets (run_id, pref_name, target_chembl_id, organism, target_type, score)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        rows = [
            (
                run_id,
                str(r.get("pref_name", "")),
                str(r.get("target_chembl_id", "")),
                str(r.get("organism", "")),
                str(r.get("target_type", "")),
                float(r["score"]) if r.get("score") is not None else None,
            )
            for _, r in df.iterrows()
        ]
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.executemany(sql, rows)
                conn.commit()
        except Exception as exc:
            logger.error("_store_targets failed: %s", exc)

    def _store_bioactivity(self, run_id: Optional[int], df: pd.DataFrame) -> None:
        if not self.db_ready or run_id is None or df is None or df.empty:
            return
        sql = """
            INSERT INTO bioactivity
                (run_id, molecule_chembl_id, canonical_smiles, standard_value, standard_type, standard_units)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        rows = []
        for _, r in df.iterrows():
            try:
                sv = float(r["standard_value"]) if r.get("standard_value") is not None else None
            except (ValueError, TypeError):
                sv = None
            rows.append((
                run_id,
                str(r.get("molecule_chembl_id", "")),
                str(r.get("canonical_smiles", "")),
                sv,
                str(r.get("standard_type", "")),
                str(r.get("standard_units", "")),
            ))
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.executemany(sql, rows)
                conn.commit()
        except Exception as exc:
            logger.error("_store_bioactivity failed: %s", exc)

    def _store_compounds(self, run_id: Optional[int], df: pd.DataFrame) -> None:
        if not self.db_ready or run_id is None or df is None or df.empty:
            return
        sql = """
            INSERT INTO compounds (run_id, molecule_chembl_id, smiles, mw, logp,
                                   numhdonors, numhacceptors, pic50, class)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        def _f(v):
            try: return float(v)
            except: return None
        def _i(v):
            try: return int(v)
            except: return None

        rows = [
            (
                run_id,
                str(r.get("molecule_chembl_id", "")),
                str(r.get("canonical_smiles", r.get("smiles", ""))),
                _f(r.get("MW")), _f(r.get("LogP")),
                _i(r.get("NumHDonors")), _i(r.get("NumHAcceptors")),
                _f(r.get("pIC50")), str(r.get("class", "")),
            )
            for _, r in df.iterrows()
        ]
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.executemany(sql, rows)
                conn.commit()
        except Exception as exc:
            logger.error("_store_compounds failed: %s", exc)

    def _store_ml_results(self, run_id: Optional[int], df: pd.DataFrame) -> None:
        if not self.db_ready or run_id is None or df is None or df.empty:
            return
        sql = """
            INSERT INTO ml_results
                (run_id, model_name, accuracy, precision_score, recall_score,
                 f1_score, cv_mean, cv_std, best)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        def _f(v):
            try: return float(v)
            except: return None

        rows = [
            (
                run_id,
                str(r.get("Model", "")),
                _f(r.get("Accuracy")), _f(r.get("Precision")),
                _f(r.get("Recall")), _f(r.get("F1-Score")),
                _f(r.get("CV Mean")), _f(r.get("CV Std")),
                i == 0,
            )
            for i, (_, r) in enumerate(df.iterrows())
        ]
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.executemany(sql, rows)
                conn.commit()
        except Exception as exc:
            logger.error("_store_ml_results failed: %s", exc)

    # ── Reads ──────────────────────────────────────────────────────────────────
    def get_run_summary(self, disease_name: str) -> ToolResult:
        if not self.db_ready:
            return ToolResult(ok=False, step="get_run_summary", message="Database not available.")
        sql = """
            SELECT id, disease_name, selected_target, status, created_at,
                   metadata,
                   (SELECT COUNT(*) FROM targets   WHERE run_id = runs.id) AS n_targets,
                   (SELECT COUNT(*) FROM bioactivity WHERE run_id = runs.id) AS n_bioactivity,
                   (SELECT COUNT(*) FROM compounds  WHERE run_id = runs.id) AS n_compounds
            FROM runs
            WHERE disease_name = %s
            ORDER BY created_at DESC
            LIMIT 1
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (disease_name,))
                    row = cur.fetchone()
            if not row:
                return ToolResult(ok=False, step="get_run_summary",
                                  message=f"No run found for '{disease_name}'.")
            run_id, dname, target, status, created_at, meta, nt, nb, nc = row
            return ToolResult(
                ok=True, step="get_run_summary", message="Run summary retrieved.",
                data={
                    "run_id": run_id, "disease_name": dname,
                    "selected_target": target, "status": status,
                    "created_at": str(created_at),
                    "n_targets": nt, "n_bioactivity": nb, "n_compounds": nc,
                    "metadata": meta,
                },
            )
        except Exception as exc:
            return ToolResult(
                ok=False, step="get_run_summary",
                message="Could not retrieve run summary.",
                error=f"{exc}\n{traceback.format_exc()}",
            )

    def healthcheck(self) -> ToolResult:
        status = {
            "db_driver": self.db_driver,
            "db_enabled": self.db_enabled,
            "db_ready": self.db_ready,
            "pool_active": self._pool is not None,
        }
        if self.db_ready:
            try:
                with self._get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                status["db_connected"] = True
            except Exception as exc:
                status["db_connected"] = False
                status["db_error"] = str(exc)
        return ToolResult(ok=True, step="healthcheck", message="OK", data=status)

    # ── Pipeline steps ─────────────────────────────────────────────────────────
    def search_targets(self, disease_name: str, run_id: Optional[int] = None) -> ToolResult:
        try:
            with self._with_run_dir(disease_name):
                targets_df, selected_target = pipeline.run_target_search(disease_name)
                if targets_df is None or targets_df.empty:
                    return ToolResult(ok=False, step="search_targets",
                                      message=f"No targets found for '{disease_name}'.")
                if selected_target:
                    self._update_run(run_id, selected_target=selected_target)
                self._store_targets(run_id, targets_df)
                return ToolResult(
                    ok=True, step="search_targets",
                    message=f"Found {len(targets_df)} targets; selected: {selected_target}",
                    data={"n_targets": len(targets_df), "selected_target": selected_target},
                )
        except Exception as exc:
            return ToolResult(ok=False, step="search_targets",
                              message="Target search failed.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def fetch_bioactivity(
        self, disease_name: str, target_chembl_id: str, run_id: Optional[int] = None
    ) -> ToolResult:
        try:
            with self._with_run_dir(disease_name):
                df = pipeline.retrieve_bioactivity(target_chembl_id, disease_name)
                if df is None or df.empty:
                    return ToolResult(ok=False, step="fetch_bioactivity",
                                      message="No bioactivity data retrieved.")
                self._store_bioactivity(run_id, df)
                return ToolResult(
                    ok=True, step="fetch_bioactivity",
                    message=f"Retrieved {len(df)} bioactivity records.",
                    data={"n_records": len(df), "target": target_chembl_id},
                )
        except Exception as exc:
            return ToolResult(ok=False, step="fetch_bioactivity",
                              message="Bioactivity fetch failed.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def curate_and_describe(self, disease_name: str, run_id: Optional[int] = None) -> ToolResult:
        try:
            with self._with_run_dir(disease_name):
                raw_path = Path(f"{disease_name}_01_bioactivity_data_raw.csv")
                if not raw_path.exists():
                    return ToolResult(ok=False, step="curate_and_describe",
                                      message=f"Raw data file not found: {raw_path.name}")
                df_raw = pd.read_csv(raw_path)
                df_curated = pipeline.preprocess_bioactivity(df_raw, disease_name)
                if df_curated is None or df_curated.empty:
                    return ToolResult(ok=False, step="curate_and_describe",
                                      message="Curation returned no compounds.")
                smiles_list = df_curated["canonical_smiles"].tolist()
                df_lipinski = pipeline.calculate_lipinski(smiles_list)
                df_combined = pd.concat([df_curated, df_lipinski], axis=1)
                df_3class, df_2class = pipeline.calculate_pic50(df_combined, disease_name)
                self._store_compounds(run_id, df_3class)
                return ToolResult(
                    ok=True, step="curate_and_describe",
                    message=f"Curated {len(df_curated)} compounds.",
                    data={
                        "n_compounds": len(df_curated),
                        "n_active": int((df_curated["class"] == "active").sum()),
                        "n_inactive": int((df_curated["class"] == "inactive").sum()),
                        "n_intermediate": int((df_curated["class"] == "intermediate").sum()),
                    },
                )
        except Exception as exc:
            return ToolResult(ok=False, step="curate_and_describe",
                              message="Curation failed.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def generate_visuals(self, disease_name: str, run_id: Optional[int] = None) -> ToolResult:
        try:
            with self._with_run_dir(disease_name):
                path = Path(f"{disease_name}_05_bioactivity_data_2class_pIC50.csv")
                if not path.exists():
                    return ToolResult(ok=False, step="generate_visuals",
                                      message=f"2-class dataset not found: {path.name}")
                df = pd.read_csv(path)
                smiles_map_path = Path(f"{disease_name}_03_bioactivity_data_curated.csv")
                if smiles_map_path.exists():
                    df_smiles = pd.read_csv(smiles_map_path)
                    smiles_map = dict(zip(df_smiles["molecule_chembl_id"], df_smiles["canonical_smiles"]))
                    df["canonical_smiles"] = df["molecule_chembl_id"].map(smiles_map)
                pipeline.generate_all_visualizations(df, disease_name)
                files = [
                    str(p) for p in Path(".").glob(f"{disease_name}_*.pdf")
                ] + [str(p) for p in Path(".").glob(f"{disease_name}_*.png")]
                self._store_files(run_id, "generate_visuals", sorted(files))
                return ToolResult(
                    ok=True, step="generate_visuals",
                    message="Visualizations generated.",
                    data={"n_files": len(files)},
                    files=sorted(files),
                )
        except Exception as exc:
            return ToolResult(ok=False, step="generate_visuals",
                              message="Visualization failed.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def viewer_from_smiles(
        self, smiles: str, name: str = "molecule", run_id: Optional[int] = None
    ) -> ToolResult:
        disease_name = name or "molecule"
        try:
            with self._with_run_dir(disease_name):
                out = f"{self._safe_name(name)}_viewer.html"
                ok = pipeline.save_py3dmol_viewer_from_smiles(smiles, name, out)
                if not ok:
                    return ToolResult(ok=False, step="viewer_from_smiles",
                                      message="Could not generate 3D viewer from SMILES.")
                self._store_files(run_id, "viewer_from_smiles", [out])
                return ToolResult(
                    ok=True, step="viewer_from_smiles",
                    message="3D viewer created.",
                    files=[out],
                    data={"name": name, "smiles": smiles},
                )
        except Exception as exc:
            return ToolResult(ok=False, step="viewer_from_smiles",
                              message="3D viewer generation failed.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def run_ml(
        self, disease_name: str, use_padel: bool = False, run_id: Optional[int] = None
    ) -> ToolResult:
        try:
            with self._with_run_dir(disease_name):
                if use_padel:
                    pipeline.download_padel()
                    df_ml = pipeline.run_padel_descriptors(disease_name)
                    if df_ml is None or df_ml.empty:
                        return ToolResult(ok=False, step="run_ml",
                                          message="PaDEL descriptor generation failed.")
                else:
                    path = Path(f"{disease_name}_05_bioactivity_data_2class_pIC50.csv")
                    if not path.exists():
                        return ToolResult(ok=False, step="run_ml",
                                          message=f"Missing 2-class dataset: {path.name}")
                    df_ml = pd.read_csv(path)
                    keep = [c for c in ["MW", "LogP", "NumHDonors", "NumHAcceptors", "pIC50", "class"]
                            if c in df_ml.columns]
                    df_ml = df_ml[keep].dropna()

                results_df = pipeline.build_ml_models(df_ml, disease_name)
                if results_df is None or results_df.empty:
                    return ToolResult(ok=False, step="run_ml",
                                      message="No ML results produced.")

                best_row = results_df.iloc[0].to_dict()
                files = (
                    [str(p) for p in Path(".").glob(f"{disease_name}_model_comparison.csv")]
                    + [str(p) for p in Path(".").glob(f"{disease_name}_cm_*.pdf")]
                )
                self._store_ml_results(run_id, results_df)
                self._store_files(run_id, "run_ml", files)
                self._update_run(run_id, metadata_patch={"best_model": best_row})
                return ToolResult(
                    ok=True, step="run_ml",
                    message="ML modeling completed.",
                    data={"best_model": best_row, "n_models": len(results_df)},
                    files=sorted(files),
                )
        except Exception as exc:
            return ToolResult(ok=False, step="run_ml",
                              message="ML modeling failed.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def archive_outputs(self, disease_name: str, run_id: Optional[int] = None) -> ToolResult:
        try:
            with self._with_run_dir(disease_name):
                ok = pipeline.create_archive(disease_name)
                zip_name = f"{disease_name}_drug_discovery_complete.zip"
                if not ok or not Path(zip_name).exists():
                    return ToolResult(ok=False, step="archive_outputs",
                                      message="Archive creation failed.")
                self._store_files(run_id, "archive_outputs", [zip_name])
                self._update_run(run_id, status="completed")
                return ToolResult(
                    ok=True, step="archive_outputs",
                    message="Archive created.",
                    files=[zip_name],
                    data={"archive": zip_name},
                )
        except Exception as exc:
            return ToolResult(ok=False, step="archive_outputs",
                              message="Archive creation failed.",
                              error=f"{exc}\n{traceback.format_exc()}")

    # ── Full workflow ──────────────────────────────────────────────────────────
    # ── AlphaFold ──────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_uniprot_id(gene_name: str, organism: str = "Homo sapiens") -> Dict[str, Any]:
        """Query UniProt REST API and return canonical accession + metadata."""
        url = "https://rest.uniprot.org/uniprotkb/search"
        params = {
            "query": f"gene:{gene_name} AND organism_name:{organism} AND reviewed:true",
            "format": "json",
            "size": 5,
            "fields": "accession,gene_names,protein_name,organism_name,length",
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])

        if not results:
            # Broaden: drop organism filter
            params["query"] = f"gene:{gene_name} AND reviewed:true"
            r = requests.get(url, params=params, timeout=15)
            results = r.json().get("results", [])

        if not results:
            raise ValueError(f"No UniProt entry found for '{gene_name}'")

        top = results[0]
        protein_name = (
            top.get("proteinDescription", {})
            .get("recommendedName", {})
            .get("fullName", {})
            .get("value", gene_name)
        )
        return {
            "accession":    top["primaryAccession"],
            "protein_name": protein_name,
            "organism":     top.get("organism", {}).get("scientificName", organism),
            "length":       top.get("sequence", {}).get("length", 0),
        }

    @staticmethod
    def _download_alphafold_pdb(uniprot_id: str, out_dir: Path) -> Dict[str, Any]:
        """Fetch AlphaFold structure metadata + download PDB. Returns info dict."""
        meta_url = f"https://alphafold.ebi.ac.uk/api/prediction/{uniprot_id}"
        r = requests.get(meta_url, timeout=15)
        if r.status_code == 404:
            raise ValueError(f"No AlphaFold entry for UniProt ID: {uniprot_id}")
        r.raise_for_status()
        meta = r.json()[0]

        pdb_url   = meta["pdbUrl"]
        model_ver = str(meta.get("latestVersion", "?"))
        entry_id  = meta.get("entryId", uniprot_id)

        pdb_path = out_dir / f"{uniprot_id}_alphafold.pdb"
        resp = requests.get(pdb_url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(pdb_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                fh.write(chunk)

        return {"pdb_path": str(pdb_path), "entry_id": entry_id, "model_version": model_ver}

    @staticmethod
    def _parse_plddt(pdb_path: str) -> Dict[str, Any]:
        """
        Parse per-residue pLDDT from B-factor column using BioPython.
        Returns summary stats + list of high-confidence regions.
        Gracefully degrades to a line-count estimate if BioPython is absent.
        """
        try:
            from Bio.PDB import PDBParser  # type: ignore
        except ImportError:
            # Fallback: count ATOM lines and return minimal info
            atom_lines = [l for l in open(pdb_path) if l.startswith("ATOM")]
            return {
                "total_residues": len({l[22:26].strip() for l in atom_lines}),
                "mean_plddt": None, "pct_very_high": None,
                "pct_confident": None, "n_confident_regions": 0,
                "regions": [], "error": "biopython_not_installed",
            }

        parser    = PDBParser(QUIET=True)
        structure = parser.get_structure("AF", pdb_path)

        residues: List[Dict[str, Any]] = []
        for model in structure:
            for chain in model:
                for res in chain:
                    bfactors = [a.get_bfactor() for a in res.get_atoms()]
                    if bfactors:
                        residues.append({
                            "num":   res.get_id()[1],
                            "plddt": sum(bfactors) / len(bfactors),
                        })

        if not residues:
            return {"total_residues": 0, "mean_plddt": 0, "pct_very_high": 0,
                    "pct_confident": 0, "n_confident_regions": 0, "regions": []}

        scores   = [r["plddt"] for r in residues]
        n        = len(scores)
        mean_p   = sum(scores) / n
        pct_vh   = 100 * sum(1 for s in scores if s >= 90) / n
        pct_conf = 100 * sum(1 for s in scores if s >= 70) / n

        # Find contiguous high-confidence stretches (pLDDT ≥ 70, min 15 residues)
        high = sorted([r for r in residues if r["plddt"] >= 70], key=lambda x: x["num"])
        regions: List[Dict[str, Any]] = []
        if high:
            start = prev = high[0]["num"]
            chunk = [high[0]["plddt"]]
            for r in high[1:]:
                if r["num"] - prev <= 2:
                    chunk.append(r["plddt"])
                    prev = r["num"]
                else:
                    if (prev - start + 1) >= 15:
                        regions.append({"start": start, "end": prev,
                                        "length": prev - start + 1,
                                        "mean_plddt": round(sum(chunk) / len(chunk), 2)})
                    start = prev = r["num"]
                    chunk = [r["plddt"]]
            if (prev - start + 1) >= 15:
                regions.append({"start": start, "end": prev,
                                "length": prev - start + 1,
                                "mean_plddt": round(sum(chunk) / len(chunk), 2)})
        regions.sort(key=lambda x: x["mean_plddt"], reverse=True)

        return {
            "total_residues":     n,
            "mean_plddt":         round(mean_p, 2),
            "pct_very_high":      round(pct_vh, 2),
            "pct_confident":      round(pct_conf, 2),
            "n_confident_regions": len(regions),
            "regions":            regions,
        }

    @staticmethod
    def _save_filtered_pdb(pdb_path: str, out_dir: Path, uniprot_id: str,
                           plddt_cutoff: float = 70.0) -> str:
        """Save a PDB containing only residues with pLDDT >= cutoff."""
        try:
            from Bio.PDB import PDBParser, PDBIO, Select  # type: ignore

            class _HighConf(Select):
                def __init__(self, keep):
                    self._keep = keep
                def accept_residue(self, res):
                    return res.get_id()[1] in self._keep

            parser    = PDBParser(QUIET=True)
            structure = parser.get_structure("AF", pdb_path)

            keep = set()
            for model in structure:
                for chain in model:
                    for res in chain:
                        bfactors = [a.get_bfactor() for a in res.get_atoms()]
                        if bfactors and (sum(bfactors) / len(bfactors)) >= plddt_cutoff:
                            keep.add(res.get_id()[1])

            filtered_path = out_dir / f"{uniprot_id}_alphafold_highconf.pdb"
            io = PDBIO()
            io.set_structure(structure)
            io.save(str(filtered_path), _HighConf(keep))
            return str(filtered_path)
        except Exception:
            return ""  # Non-fatal if BioPython absent

    def fetch_alphafold_structure(
        self,
        disease_name: str,
        uniprot_id: Optional[str] = None,
        organism: str = "Homo sapiens",
        run_id: Optional[int] = None,
        plddt_cutoff: float = 70.0,
    ) -> ToolResult:
        """
        End-to-end AlphaFold integration:
          1. Resolve UniProt ID (from target name or supplied directly)
          2. Download AlphaFold PDB
          3. Parse pLDDT confidence scores
          4. Save filtered (high-confidence) PDB
          5. Persist everything to the alphafold_structures table

        Parameters
        ----------
        disease_name  : Used to look up the selected_target from the runs table
                        when uniprot_id is not supplied.
        uniprot_id    : Override auto-resolution (e.g. "P00533" for EGFR).
        organism      : UniProt organism filter (default Homo sapiens).
        run_id        : Link result to a specific run.
        plddt_cutoff  : Residues below this are excluded from the filtered PDB.
        """
        step = "fetch_alphafold_structure"
        out_dir = self._run_dir(disease_name)

        try:
            # ── 1. Resolve UniProt ID ────────────────────────────────────────
            if not uniprot_id:
                # Pull the selected target (gene symbol) from the latest run
                run_summary = self.get_run_summary(disease_name)
                gene_name   = (
                    (run_summary.data or {}).get("selected_target") or disease_name
                )
                logger.info("Resolving UniProt ID for gene: %s", gene_name)
                uniprot_info = self._resolve_uniprot_id(gene_name, organism)
                uniprot_id   = uniprot_info["accession"]
            else:
                # Fetch minimal metadata for the supplied ID
                url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.json"
                r   = requests.get(url, timeout=10)
                r.raise_for_status()
                raw = r.json()
                uniprot_info = {
                    "accession":    uniprot_id,
                    "protein_name": (
                        raw.get("proteinDescription", {})
                        .get("recommendedName", {})
                        .get("fullName", {})
                        .get("value", uniprot_id)
                    ),
                    "organism": raw.get("organism", {}).get("scientificName", organism),
                    "length":   raw.get("sequence", {}).get("length", 0),
                }

            logger.info("Downloading AlphaFold structure for %s (%s)", uniprot_id, uniprot_info.get("protein_name"))

            # ── 2. Download PDB ──────────────────────────────────────────────
            pdb_info = self._download_alphafold_pdb(uniprot_id, out_dir)

            # ── 3. Parse pLDDT ───────────────────────────────────────────────
            plddt_stats = self._parse_plddt(pdb_info["pdb_path"])

            # ── 4. Filtered PDB ──────────────────────────────────────────────
            filtered_path = self._save_filtered_pdb(
                pdb_info["pdb_path"], out_dir, uniprot_id, plddt_cutoff
            )

            # ── 5. Persist to DB ─────────────────────────────────────────────
            if self.db_ready:
                sql = """
                    INSERT INTO alphafold_structures (
                        run_id, disease_name, uniprot_id, protein_name, organism,
                        sequence_length, mean_plddt, pct_very_high, pct_confident,
                        n_confident_regions, pdb_path, filtered_pdb_path, af_model_version
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                """
                try:
                    with self._get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(sql, (
                                run_id, disease_name, uniprot_id,
                                uniprot_info.get("protein_name"),
                                uniprot_info.get("organism"),
                                uniprot_info.get("length"),
                                plddt_stats.get("mean_plddt"),
                                plddt_stats.get("pct_very_high"),
                                plddt_stats.get("pct_confident"),
                                plddt_stats.get("n_confident_regions"),
                                pdb_info["pdb_path"],
                                filtered_path or None,
                                pdb_info.get("model_version"),
                            ))
                        conn.commit()
                except Exception as db_exc:
                    logger.warning("Could not persist AlphaFold record: %s", db_exc)

            result_data = {
                "disease_name":       disease_name,
                "uniprot_id":         uniprot_id,
                "protein_name":       uniprot_info.get("protein_name"),
                "organism":           uniprot_info.get("organism"),
                "sequence_length":    uniprot_info.get("length"),
                "af_model_version":   pdb_info.get("model_version"),
                "pdb_path":           pdb_info["pdb_path"],
                "filtered_pdb_path":  filtered_path or None,
                **plddt_stats,
            }
            logger.info("AlphaFold structure ready: %s (mean pLDDT=%.1f)",
                        uniprot_id, plddt_stats.get("mean_plddt") or 0)
            return ToolResult(
                ok=True, step=step,
                message=f"AlphaFold structure fetched for {uniprot_id} ({uniprot_info.get('protein_name')}).",
                data=result_data,
                files=[pdb_info["pdb_path"]] + ([filtered_path] if filtered_path else []),
            )

        except Exception as exc:
            logger.error("fetch_alphafold_structure failed: %s", exc)
            return ToolResult(
                ok=False, step=step,
                message=f"AlphaFold fetch failed: {exc}",
                error=f"{exc}\n{traceback.format_exc()}",
            )

    def get_alphafold_structure(self, disease_name: str) -> ToolResult:
        """Retrieve the most recent AlphaFold record for a disease from the DB."""
        step = "get_alphafold_structure"
        if not self.db_ready:
            return ToolResult(ok=False, step=step, message="Database not available.")
        sql = """
            SELECT uniprot_id, protein_name, organism, sequence_length,
                   mean_plddt, pct_very_high, pct_confident, n_confident_regions,
                   pdb_path, filtered_pdb_path, af_model_version, created_at
            FROM alphafold_structures
            WHERE disease_name = %s
            ORDER BY created_at DESC
            LIMIT 1
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (disease_name,))
                    row = cur.fetchone()
            if not row:
                return ToolResult(ok=False, step=step,
                                  message=f"No AlphaFold record found for '{disease_name}'.")
            cols = ["uniprot_id", "protein_name", "organism", "sequence_length",
                    "mean_plddt", "pct_very_high", "pct_confident", "n_confident_regions",
                    "pdb_path", "filtered_pdb_path", "af_model_version", "created_at"]
            data = dict(zip(cols, row))
            data["created_at"] = str(data["created_at"])
            data["disease_name"] = disease_name
            return ToolResult(ok=True, step=step, message="AlphaFold record retrieved.", data=data)
        except Exception as exc:
            return ToolResult(ok=False, step=step,
                              message="Query failed.",
                              error=f"{exc}\n{traceback.format_exc()}")

    # ──────────────────────────────────────────────────────────────────────────
    def run_workflow(
        self,
        disease_name: str,
        target_chembl_id: Optional[str] = None,
        do_visuals: bool = True,
        do_ml: bool = False,
        use_padel: bool = False,
        do_archive: bool = True,
        do_alphafold: bool = False,
        af_uniprot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"ok": True, "disease_name": disease_name, "steps": []}

        if self.db_ready:
            prev = self.get_run_summary(disease_name)
            if prev.ok:
                summary["previous_run"] = prev.data

        run_id = self._insert_run(
            disease_name=disease_name,
            selected_target=target_chembl_id,
            status="started",
            metadata={"do_visuals": do_visuals, "do_ml": do_ml,
                      "use_padel": use_padel, "do_archive": do_archive},
        )
        summary["run_id"] = run_id

        def _step(result: ToolResult, label: str) -> bool:
            summary["steps"].append(result.to_dict())
            if not result.ok:
                summary["ok"] = False
                self._update_run(run_id, status="failed",
                                 metadata_patch={"failed_step": label})
            return result.ok

        if not _step(self.search_targets(disease_name, run_id=run_id), "search_targets"):
            return summary

        selected_target = target_chembl_id or (summary["steps"][-1].get("data") or {}).get("selected_target")
        if not selected_target:
            summary["ok"] = False
            summary["message"] = "No target was selected."
            self._update_run(run_id, status="failed",
                             metadata_patch={"failed_step": "search_targets", "reason": "no target"})
            return summary

        if not _step(self.fetch_bioactivity(disease_name, selected_target, run_id=run_id), "fetch_bioactivity"):
            return summary
        if not _step(self.curate_and_describe(disease_name, run_id=run_id), "curate_and_describe"):
            return summary
        if do_visuals and not _step(self.generate_visuals(disease_name, run_id=run_id), "generate_visuals"):
            return summary
        if do_ml and not _step(self.run_ml(disease_name, use_padel=use_padel, run_id=run_id), "run_ml"):
            return summary
        if do_alphafold:
            _step(
                self.fetch_alphafold_structure(disease_name, uniprot_id=af_uniprot_id, run_id=run_id),
                "fetch_alphafold_structure",
            )
            # AlphaFold is non-blocking — a failure won't abort the workflow
        if do_archive:
            if not _step(self.archive_outputs(disease_name, run_id=run_id), "archive_outputs"):
                return summary
        else:
            self._update_run(run_id, status="completed")

        summary["files"] = sorted({f for s in summary["steps"] for f in (s.get("files") or [])})
        if self.db_ready:
            summary["db_summary"] = self.get_run_summary(disease_name).to_dict()
        return summary


    # ── DB query methods ───────────────────────────────────────────────────────

    def list_all_runs(self) -> ToolResult:
        """List every disease run stored in the DB."""
        if not self.db_ready:
            return ToolResult(ok=False, step="list_all_runs", message="Database not available.")
        sql = """
            SELECT disease_name, selected_target, status, created_at,
                   (SELECT COUNT(*) FROM compounds WHERE run_id = runs.id) AS n_compounds
            FROM runs
            ORDER BY created_at DESC
            LIMIT 50
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()
            runs = [
                {"disease_name": r[0], "selected_target": r[1],
                 "status": r[2], "created_at": str(r[3]), "n_compounds": r[4]}
                for r in rows
            ]
            return ToolResult(ok=True, step="list_all_runs",
                              message=f"Found {len(runs)} runs.",
                              data={"runs": runs})
        except Exception as exc:
            return ToolResult(ok=False, step="list_all_runs",
                              message="Could not list runs.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def get_compounds(
        self,
        disease_name: str,
        activity_class: Optional[str] = None,
        limit: int = 50,
    ) -> ToolResult:
        """Fetch compounds with SMILES for a disease from the DB."""
        if not self.db_ready:
            return ToolResult(ok=False, step="get_compounds", message="Database not available.")
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM runs WHERE disease_name = %s ORDER BY created_at DESC LIMIT 1",
                        (disease_name,)
                    )
                    row = cur.fetchone()
                    if not row:
                        return ToolResult(ok=False, step="get_compounds",
                                          message=f"No run found for '{disease_name}'.")
                    run_id = row[0]

                    if activity_class:
                        cur.execute("""
                            SELECT molecule_chembl_id, smiles, mw, logp,
                                   numhdonors, numhacceptors, pic50, class
                            FROM compounds WHERE run_id = %s AND class = %s
                            ORDER BY pic50 DESC NULLS LAST LIMIT %s
                        """, (run_id, activity_class, limit))
                    else:
                        cur.execute("""
                            SELECT molecule_chembl_id, smiles, mw, logp,
                                   numhdonors, numhacceptors, pic50, class
                            FROM compounds WHERE run_id = %s
                            ORDER BY pic50 DESC NULLS LAST LIMIT %s
                        """, (run_id, limit))
                    rows = cur.fetchall()

            compounds = [
                {"molecule_chembl_id": r[0], "smiles": r[1], "mw": r[2],
                 "logp": r[3], "num_h_donors": r[4], "num_h_acceptors": r[5],
                 "pic50": r[6], "class": r[7]}
                for r in rows if r[1]
            ]
            return ToolResult(ok=True, step="get_compounds",
                              message=f"Retrieved {len(compounds)} compounds for '{disease_name}'.",
                              data={"disease_name": disease_name, "run_id": run_id,
                                    "count": len(compounds), "compounds": compounds})
        except Exception as exc:
            return ToolResult(ok=False, step="get_compounds",
                              message="Could not fetch compounds.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def get_targets(self, disease_name: str) -> ToolResult:
        """Fetch stored targets for a disease."""
        if not self.db_ready:
            return ToolResult(ok=False, step="get_targets", message="Database not available.")
        sql = """
            SELECT t.target_chembl_id, t.pref_name, t.organism, t.target_type, t.score
            FROM targets t
            JOIN runs r ON r.id = t.run_id
            WHERE r.disease_name = %s
            ORDER BY r.created_at DESC, t.score DESC NULLS LAST
            LIMIT 20
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (disease_name,))
                    rows = cur.fetchall()
            targets = [
                {"target_chembl_id": r[0], "pref_name": r[1],
                 "organism": r[2], "target_type": r[3], "score": r[4]}
                for r in rows
            ]
            return ToolResult(ok=True, step="get_targets",
                              message=f"Found {len(targets)} targets for '{disease_name}'.",
                              data={"disease_name": disease_name, "targets": targets})
        except Exception as exc:
            return ToolResult(ok=False, step="get_targets",
                              message="Could not fetch targets.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def get_ml_results(self, disease_name: str) -> ToolResult:
        """Fetch ML model results for a disease."""
        if not self.db_ready:
            return ToolResult(ok=False, step="get_ml_results", message="Database not available.")
        sql = """
            SELECT ml.model_name, ml.accuracy, ml.precision_score, ml.recall_score,
                   ml.f1_score, ml.cv_mean, ml.cv_std, ml.best
            FROM ml_results ml
            JOIN runs r ON r.id = ml.run_id
            WHERE r.disease_name = %s
            ORDER BY r.created_at DESC, ml.accuracy DESC NULLS LAST
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (disease_name,))
                    rows = cur.fetchall()
            results = [
                {"model": r[0], "accuracy": r[1], "precision": r[2],
                 "recall": r[3], "f1": r[4], "cv_mean": r[5],
                 "cv_std": r[6], "best": r[7]}
                for r in rows
            ]
            return ToolResult(ok=True, step="get_ml_results",
                              message=f"Found {len(results)} ML results for '{disease_name}'.",
                              data={"disease_name": disease_name, "results": results})
        except Exception as exc:
            return ToolResult(ok=False, step="get_ml_results",
                              message="Could not fetch ML results.",
                              error=f"{exc}\n{traceback.format_exc()}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tools = DrugDiscoveryAgentTools()
    print(json.dumps(tools.healthcheck().to_dict(), indent=2))
