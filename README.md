# AI-Powered Drug Discovery Agent

An end-to-end drug discovery pipeline that automates target identification, bioactivity screening, molecular descriptor calculation, machine learning classification, and 3D visualisation — all served through a REST API and augmented by a conversational LLM agent with persistent memory.

**Author:** Paul Alemoh · Dept. of Bioinformatics, UALR · Spring 2026

---

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Server](#running-the-server)
- [Running the Pipeline](#running-the-pipeline)
- [API Reference](#api-reference)
- [RAG Document Ingestion](#rag-document-ingestion)
- [Database Schema](#database-schema)
- [Pipeline Output Files](#pipeline-output-files)
- [PaDEL Fingerprints](#padel-fingerprints)

---

## Overview

The system integrates five layers:

1. **ChEMBL pipeline** — queries targets by disease name, fetches IC50 bioactivity data, applies Lipinski's Rule of Five, converts IC50 to pIC50, and trains ML classifiers.
2. **FastAPI server** — exposes 20+ REST endpoints for workflow execution, compound queries, chat, and file downloads.
3. **LLM agent** — GPT-4.1-mini powered chat that grounds responses in live database context and retrieved documents.
4. **RAG layer** — PGVector semantic search over ingested scientific documents (LangChain + pgvector).
5. **PostgreSQL persistence** — all pipeline outputs, conversation history, and document embeddings stored in a shared `drugdb` database.

---

## System Architecture

```
REST clients / SMILES 3D viewer
         │
         ▼
  FastAPI  (agent_server_llm.py)
         │
         ▼
  DrugDiscoveryAgentTools          ◄──► OpenAI GPT-4.1-mini-change to openrouter deepseek
  (agent_tools_db_memory_openai.py)      embed-3-small (RAG)
         │
    ┌────┴─────────────────┐
    ▼                      ▼
BaseTools              document_rag.py
(pipeline + ChEMBL)    (PGVector retrieval)
    │                      │
    └──────────┬───────────┘
               ▼
        PostgreSQL  drugdb
  ┌──────────────────────────────┐
  │ runs · targets · compounds   │
  │ bioactivity · ml_results     │
  │ conversations                │
  │ documents · document_chunks  │
  │ pgvector collection          │
  └──────────────────────────────┘
```

---

## Project Structure

```
.
├── agent_server_llm.py               # FastAPI application and all routes
├── agent_tools_db_memory_openai.py   # LLM chat agent with memory and RAG
├── agent_tools_db_fixed.py           # BaseTools: pipeline + DB persistence
├── drug_discovery_advanced_py3dmol.py# Core pipeline functions
├── document_rag.py                   # RAG layer: PGVector + LangChain
├── ingest.py                         # CLI for ingesting documents into PostgreSQL
├── smiles_3d_viewer.html             # Static 3D molecular viewer (served at /viewer)
├── requirements.txt
├── .env                              # Environment variables (not committed)
└── runs/                             # Pipeline output directory (auto-created)
    └── {disease_name}/
        ├── *.csv                     # Bioactivity and ML output tables
        ├── *.pdf                     # Visualisation plots
        └── *.html                    # Interactive 3D viewers
```

---

## Installation

### 1. Prerequisites

- Python 3.10+
- PostgreSQL 14+ with the [pgvector extension](https://github.com/pgvector/pgvector)
- Java JDK 11+ (only required for PaDEL fingerprints)

### 2. Enable pgvector

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 3. Create the database

```sql
CREATE DATABASE drugdb;
```

### 4. Install Python dependencies

```bash
pip install -r requirements.txt
```

> **Note on psycopg drivers:** `requirements.txt` includes `psycopg[binary]` (psycopg3).
> If you encounter installation issues, comment that line out and uncomment `psycopg2-binary` instead.

### 5. Create the `.env` file

```bash
cp .env.example .env   # or create it manually — see Configuration below
```

---

## Configuration

Create a `.env` file in the project root with the following variables:

```env
# PostgreSQL
PGHOST=localhost
PGPORT=5432
PGDATABASE=drugdb
PGUSER=postgres
PGPASSWORD=your_password

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini
```

All variables have sensible defaults except `OPENAI_API_KEY`. The pipeline and database will function without it — the chat agent falls back to a rule-based handler when no key is present.

---

## Running the Server

```bash
uvicorn agent_server_llm:app --reload --port 8000
```

The server starts at `http://localhost:8000`.

- API documentation: `http://localhost:8000/docs`
- 3D molecular viewer: `http://localhost:8000/viewer`
- Health check: `http://localhost:8000/health`

---

## Running the Pipeline

### Via the REST API (recommended)

```bash
curl -X POST http://localhost:8000/workflow/run \
  -H "Content-Type: application/json" \
  -d '{
    "disease_name": "diphtheria",
    "do_visuals": true,
    "do_ml": false,
    "do_archive": true
  }'
```

### Via the Python API

```python
from drug_discovery_advanced_py3dmol import run_pipeline

summary = run_pipeline(
    disease_name="diphtheria",
    do_visuals=True,
    do_padel=False,
    do_ml=True,
    do_archive=True,
)
print(summary["selected_target"])
```

### Via the CLI

```bash
python drug_discovery_advanced_py3dmol.py
```

The CLI prompts for disease name, optional target override, and whether to run PaDEL.

---

## API Reference

### Workflow

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/workflow/run` | Run the full pipeline end-to-end |
| `POST` | `/targets/search` | Search ChEMBL targets for a disease |
| `POST` | `/bioactivity/fetch` | Fetch IC50 records for a target |
| `POST` | `/curate/run` | Apply Lipinski curation and pIC50 conversion |
| `POST` | `/visuals/run` | Generate visualisation plots |
| `POST` | `/ml/run` | Train and evaluate ML classifiers |

### Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/chat` | Send a message to the LLM agent |
| `GET` | `/chat/history/{session_id}` | Retrieve conversation history |
| `DELETE` | `/chat/history/{session_id}` | Clear a session |

### Query

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/compounds/{disease}` | Fetch compounds for a disease run |
| `GET` | `/targets/{disease}` | Fetch stored targets for a disease |
| `GET` | `/ml-results/{disease}` | Fetch ML model comparison results |
| `GET` | `/db/runs` | List all stored disease runs |

### Files & Viewer

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/viewer` | Serve the 3D SMILES viewer |
| `POST` | `/viewer/smiles` | Generate a 3D viewer for a SMILES string |
| `GET` | `/runs` | List all run directories |
| `GET` | `/runs/{run_name}/files` | List files in a run directory |
| `GET` | `/files/{run_name}/{path}` | Download a specific output file |

### WorkflowRequest body

```json
{
  "disease_name": "diphtheria",
  "target_chembl_id": null,
  "do_visuals": true,
  "do_ml": false,
  "use_padel": false,
  "do_archive": true
}
```

---

## RAG Document Ingestion

The system supports two ingestion paths. Both write to PostgreSQL but serve different purposes:

| Path | Tool | Purpose |
|------|------|---------|
| Raw text store | `ingest.py` | Audit trail, full-text reference |
| Vector store | `document_rag.py` | Semantic similarity search |

### Ingest a document via the CLI

```bash
# From a text file
python ingest.py --title "Diphtheria Toxin Review" --source "pubmed" --file review.txt

# From a PDF
python ingest.py --title "ChEMBL Target Analysis" --source "chembl" --file report.pdf

# Inline text
python ingest.py --title "Study Notes" --text "The compound showed IC50 of 4.2 nM..."
```

### Ingest programmatically

```python
from document_rag import ingest_text

chunks_stored = ingest_text(
    title="Diphtheria inhibitor study",
    text="Full document text here...",
    source="manual",
)
print(f"Stored {chunks_stored} chunks")
```

Once ingested, the agent will automatically retrieve relevant chunks when you ask scientific questions via `/chat`.

---

## Database Schema

### Core pipeline tables

```
runs            — disease_name, selected_target, status, metadata JSONB
targets         — target_chembl_id, pref_name, organism, target_type, score
bioactivity     — molecule_id, standard_value, standard_type, run_id FK
compounds       — SMILES, MW, LogP, NumHDonors, NumHAcceptors, pIC50, class
ml_results      — model_name, accuracy, precision, recall, f1, cv_mean, cv_std, best
```

### Conversation memory

```
conversations   — session_id, user_id, role, message, metadata JSONB, created_at
```

### RAG document store

```
documents       — title, source, content TEXT, metadata JSONB
document_chunks — document_id FK, chunk_index, content TEXT, metadata JSONB
```

The pgvector collection (`drug_documents`) is managed automatically by LangChain and stored in PostgreSQL alongside the above tables.

---

## Pipeline Output Files

After a run for disease `diphtheria`, the following files are created in `runs/diphtheria/`:

| File | Description |
|------|-------------|
| `diphtheria_01_bioactivity_data_raw.csv` | Raw ChEMBL IC50 records |
| `diphtheria_02_bioactivity_data_preprocessed.csv` | After deduplication and null removal |
| `diphtheria_03_bioactivity_data_curated.csv` | With activity class labels |
| `diphtheria_04_bioactivity_data_3class_pIC50.csv` | With pIC50, all 3 classes |
| `diphtheria_05_bioactivity_data_2class_pIC50.csv` | Active/inactive only |
| `diphtheria_06_bioactivity_data_ml_ready.csv` | With PaDEL fingerprints (if enabled) |
| `diphtheria_model_comparison.csv` | ML model accuracy comparison |
| `diphtheria_class_distribution.pdf` | Bar chart of class counts |
| `diphtheria_mw_distribution.pdf` | MW histogram by class |
| `diphtheria_property_boxplots.pdf` | Lipinski descriptor box plots |
| `diphtheria_correlation_heatmap.pdf` | Descriptor correlation matrix |
| `diphtheria_radar_chart.pdf` | Radar chart of mean properties |
| `diphtheria_scatter_*.pdf` | MW/LogP/pIC50 scatter plots |
| `diphtheria_molecule_grid.png` | 2D molecular structure grid |
| `3d_visualizations/` | Interactive HTML viewers and 3D plots |
| `diphtheria_drug_discovery_complete.zip` | Archive of all the above |

---

## PaDEL Fingerprints

PaDEL-Descriptor generates 881-dimensional binary molecular fingerprints. It requires Java and is downloaded automatically by the pipeline when `use_padel=True`.

**Prerequisites:**
```bash
# macOS
brew install openjdk@11

# Ubuntu / Debian
sudo apt install default-jdk
```

**Enable via API:**
```json
{
  "disease_name": "diphtheria",
  "do_ml": true,
  "use_padel": true
}
```

**Enable via Python:**
```python
run_pipeline(disease_name="diphtheria", do_padel=True, do_ml=True)
```

When disabled, ML models train on the four Lipinski descriptors (MW, LogP, NumHDonors, NumHAcceptors) instead, which is faster and sufficient for most exploratory analyses.
