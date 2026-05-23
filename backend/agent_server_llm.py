from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_tools_db_memory_openai import DrugDiscoveryAgentTools

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Drug Discovery Agent API",
    version="1.2.0",
    description="AI-powered drug discovery — Paul Alemoh, Dept. of Bioinformatics, UALR",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve the 3D SMILES viewer frontend as a static file ──────────────────────
# Place smiles_3d_viewer.html in the same directory as this script.
# Access it at: http://localhost:8000/viewer
_FRONTEND = Path(__file__).resolve().parent / "smiles_3d_viewer.html"

# ── Serve the AlphaFold viewer frontend ───────────────────────────────────────
# Place alphafold_viewer.html in the same directory as this script.
# Access it at: http://localhost:8000/alphafold-viewer
_AF_FRONTEND = Path(__file__).resolve().parent / "alphafold_viewer.html"

@app.get("/viewer", include_in_schema=False)
def serve_frontend():
    if _FRONTEND.exists():
        return FileResponse(_FRONTEND, media_type="text/html")
    return {"ok": False, "message": "Frontend file not found. Place smiles_3d_viewer.html here."}

@app.get("/alphafold-viewer", include_in_schema=False)
def serve_alphafold_frontend():
    if _AF_FRONTEND.exists():
        return FileResponse(_AF_FRONTEND, media_type="text/html")
    return {"ok": False, "message": "AlphaFold viewer not found. Place alphafold_viewer.html here."}

# ── Tools ──────────────────────────────────────────────────────────────────────
tools = DrugDiscoveryAgentTools(base_output_dir="runs")
logger.info("DrugDiscoveryAgentTools initialized. DB ready: %s", tools.db_ready)


# ── Request models ─────────────────────────────────────────────────────────────
class WorkflowRequest(BaseModel):
    disease_name: str = Field(..., description="Disease or condition name, e.g. diphtheria")
    target_chembl_id: Optional[str] = Field(default=None, description="Optional ChEMBL target ID")
    do_visuals: bool = True
    do_ml: bool = False
    use_padel: bool = False
    do_archive: bool = True
    do_alphafold: bool = False
    af_uniprot_id: Optional[str] = Field(default=None, description="Override UniProt ID for AlphaFold lookup")


class TargetRequest(BaseModel):
    disease_name: str


class BioactivityRequest(BaseModel):
    disease_name: str
    target_chembl_id: str


class SmilesViewerRequest(BaseModel):
    smiles: str
    name: str = "molecule"


class AlphaFoldRequest(BaseModel):
    disease_name: str
    uniprot_id: Optional[str] = Field(default=None, description="Override UniProt accession, e.g. P00533")
    organism: str = Field(default="Homo sapiens")
    plddt_cutoff: float = Field(default=70.0, ge=0.0, le=100.0)


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Conversation/session identifier")
    message: str = Field(..., description="User message")
    user_id: Optional[str] = Field(default=None, description="Optional user identifier")


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "service": "drug-discovery-agent",
        "version": "1.2.0",
        "status": "ok",
        "frontend": "/viewer",
        "docs": "/docs",
        "routes": {
            "system":    ["/health", "/docs"],
            "workflow":  ["/workflow/run"],
            "pipeline":  ["/targets/search", "/bioactivity/fetch",
                          "/curate/run", "/visuals/run", "/ml/run"],
            "viewer":    ["/viewer", "/viewer/smiles", "/alphafold-viewer"],
            "runs":      ["/runs", "/runs/{run_name}/files",
                          "/files/{run_name}/{file_path}"],
            "alphafold": ["/alphafold/fetch", "/alphafold/{disease_name}",
                          "/alphafold/{disease_name}/pdb"],
            "chat":      ["/chat", "/chat/history/{session_id}"],
        },
    }


@app.get("/health")
def health():
    return tools.healthcheck().to_dict()


# ── Run listing ────────────────────────────────────────────────────────────────
@app.get("/runs")
def list_runs():
    runs_dir = Path("runs")
    if not runs_dir.exists():
        return {"runs": []}
    return {
        "runs": [
            {
                "run_name": run.name,
                "file_count": sum(1 for f in run.rglob("*") if f.is_file()),
            }
            for run in runs_dir.iterdir()
            if run.is_dir()
        ]
    }


@app.get("/runs/{run_name}/files")
def list_run_files(run_name: str):
    run_path = Path("runs") / run_name
    if not run_path.exists():
        return {"files": []}
    return {
        "files": [
            {
                "name": f.name,
                "relative_path": str(f.relative_to(run_path)),
                "download_url": f"/files/{run_name}/{f.relative_to(run_path)}",
            }
            for f in run_path.rglob("*")
            if f.is_file()
        ]
    }


# ── Pipeline endpoints ─────────────────────────────────────────────────────────
@app.post("/workflow/run")
def run_workflow(req: WorkflowRequest):
    return tools.run_workflow(
        disease_name=req.disease_name,
        target_chembl_id=req.target_chembl_id,
        do_visuals=req.do_visuals,
        do_ml=req.do_ml,
        use_padel=req.use_padel,
        do_archive=req.do_archive,
        do_alphafold=req.do_alphafold,
        af_uniprot_id=req.af_uniprot_id,
    )


@app.post("/targets/search")
def search_targets(req: TargetRequest):
    return tools.search_targets(req.disease_name).to_dict()


@app.post("/bioactivity/fetch")
def fetch_bioactivity(req: BioactivityRequest):
    return tools.fetch_bioactivity(req.disease_name, req.target_chembl_id).to_dict()


@app.post("/curate/run")
def curate_run(req: TargetRequest):
    return tools.curate_and_describe(req.disease_name).to_dict()


@app.post("/visuals/run")
def visuals_run(req: TargetRequest):
    return tools.generate_visuals(req.disease_name).to_dict()


@app.post("/ml/run")
def ml_run(req: WorkflowRequest):
    return tools.run_ml(req.disease_name, use_padel=req.use_padel).to_dict()


@app.post("/viewer/smiles")
def viewer_from_smiles(req: SmilesViewerRequest):
    return tools.viewer_from_smiles(req.smiles, req.name).to_dict()


# ── Chat ───────────────────────────────────────────────────────────────────────
@app.post("/chat")
def chat(req: ChatRequest):
    return tools.chat(session_id=req.session_id, message=req.message, user_id=req.user_id)


@app.get("/chat/history/{session_id}")
def get_chat_history(session_id: str, limit: int = 20):
    return tools.get_conversation_history(session_id, limit=limit).to_dict()


@app.delete("/chat/history/{session_id}")
def clear_chat_history(session_id: str):
    return tools.clear_conversation(session_id).to_dict()


# ── File download ──────────────────────────────────────────────────────────────
@app.get("/files/{run_name}/{file_path:path}")
def get_file(run_name: str, file_path: str):
    base = Path("runs") / run_name
    target = (base / file_path).resolve()
    if not str(target).startswith(str(base.resolve())):
        return {"ok": False, "message": "Invalid path."}
    if not target.exists():
        return {"ok": False, "message": "File not found."}
    return FileResponse(target)


# ── Database query endpoints ───────────────────────────────────────────────────

@app.get("/db/runs")
def list_all_runs():
    """List every disease run stored in the database."""
    return tools.list_all_runs().to_dict()


@app.get("/compounds/{disease_name}")
def get_compounds(
    disease_name: str,
    activity_class: Optional[str] = None,
    limit: int = 50,
):
    """
    Fetch compounds with SMILES for a disease from the database.
    GET /compounds/diphtheria
    GET /compounds/diphtheria?activity_class=active&limit=20
    """
    return tools.get_compounds(
        disease_name=disease_name,
        activity_class=activity_class,
        limit=limit,
    ).to_dict()


@app.get("/targets/{disease_name}")
def get_targets(disease_name: str):
    """Fetch stored targets for a disease."""
    return tools.get_targets(disease_name).to_dict()


@app.get("/ml-results/{disease_name}")
def get_ml_results(disease_name: str):
    """Fetch ML model comparison results."""
    return tools.get_ml_results(disease_name).to_dict()


# ── AlphaFold ──────────────────────────────────────────────────────────────────

@app.post("/alphafold/fetch")
def alphafold_fetch(req: AlphaFoldRequest):
    """
    Resolve UniProt ID → download AlphaFold PDB → parse pLDDT → persist to DB.

    - `disease_name`  used to infer the target gene from the latest run.
    - `uniprot_id`    (optional) skips UniProt resolution; use known accession directly.
    - `plddt_cutoff`  residues below this are excluded from the high-confidence PDB (default 70).

    Example body:
    ```json
    {"disease_name": "diphtheria"}
    {"disease_name": "cancer", "uniprot_id": "P00533", "plddt_cutoff": 75}
    ```
    """
    return tools.fetch_alphafold_structure(
        disease_name=req.disease_name,
        uniprot_id=req.uniprot_id,
        organism=req.organism,
        plddt_cutoff=req.plddt_cutoff,
    ).to_dict()


@app.get("/alphafold/{disease_name}")
def alphafold_get(disease_name: str):
    """
    Retrieve the most recent stored AlphaFold structure record for a disease.
    Returns pLDDT stats, file paths, and model version.
    """
    return tools.get_alphafold_structure(disease_name).to_dict()


@app.get("/alphafold/{disease_name}/pdb")
def alphafold_download_pdb(disease_name: str, filtered: bool = False):
    """
    Download the PDB file for a disease's AlphaFold structure.
    Set `?filtered=true` to download only the high-confidence residues (pLDDT ≥ cutoff).
    """
    record = tools.get_alphafold_structure(disease_name)
    if not record.ok or not record.data:
        return {"ok": False, "message": record.message}

    pdb_key  = "filtered_pdb_path" if filtered else "pdb_path"
    pdb_file = record.data.get(pdb_key)
    if not pdb_file or not Path(pdb_file).exists():
        alt_key  = "pdb_path" if filtered else "filtered_pdb_path"
        pdb_file = record.data.get(alt_key)
        if not pdb_file or not Path(pdb_file).exists():
            return {"ok": False, "message": f"PDB file not found on disk: {pdb_file}"}

    fname = Path(pdb_file).name
    return FileResponse(pdb_file, media_type="chemical/x-pdb",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

