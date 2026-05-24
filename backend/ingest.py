"""
ingest.py — Ingest documents into PostgreSQL (raw text + chunks).

Fix: `from pypdf import PdfReader` was in the middle of the file
     (after function definitions). Moved to the top with all other imports.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ── PDF support (optional) ─────────────────────────────────────────────────────
try:
    from pypdf import PdfReader
    _PDF_AVAILABLE = True
except ImportError:
    PdfReader = None        # type: ignore
    _PDF_AVAILABLE = False

# ── DB drivers ─────────────────────────────────────────────────────────────────
try:
    import psycopg
except ImportError:
    psycopg = None

try:
    import psycopg2
except ImportError:
    psycopg2 = None

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ── Env + connection ───────────────────────────────────────────────────────────
def load_env() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)


def get_conn():
    cfg = {
        "dbname":   os.getenv("PGDATABASE", "drugdb"),
        "user":     os.getenv("PGUSER"),
        "password": os.getenv("PGPASSWORD", ""),
        "host":     os.getenv("PGHOST", "localhost"),
        "port":     int(os.getenv("PGPORT", "5432")),
    }
    if psycopg is not None:
        return psycopg.connect(**cfg)
    if psycopg2 is not None:
        return psycopg2.connect(**cfg)
    raise RuntimeError(
        "No PostgreSQL driver found. "
        "Install one of: pip install psycopg[binary]  or  pip install psycopg2-binary"
    )


# ── Schema ─────────────────────────────────────────────────────────────────────
def ensure_tables(conn) -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS documents (
        id SERIAL PRIMARY KEY,
        title TEXT,
        source TEXT,
        content TEXT NOT NULL,
        metadata JSONB,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS document_chunks (
        id SERIAL PRIMARY KEY,
        document_id INT REFERENCES documents(id) ON DELETE CASCADE,
        chunk_index INT NOT NULL,
        content TEXT NOT NULL,
        metadata JSONB,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_id ON document_chunks(document_id);
    CREATE INDEX IF NOT EXISTS idx_documents_created  ON documents(created_at);
    """
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()


# ── Text readers ───────────────────────────────────────────────────────────────
def read_pdf(file_path: str) -> str:
    if not _PDF_AVAILABLE:
        raise RuntimeError("pypdf is not installed. Run: pip install pypdf")
    reader = PdfReader(file_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def read_text(file_path: Optional[str], inline_text: Optional[str]) -> str:
    if inline_text:
        return inline_text
    if not file_path:
        raise ValueError("Provide either --file or --text.")
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return read_pdf(str(path)) if path.suffix.lower() == ".pdf" else path.read_text(encoding="utf-8")


# ── Ingest ─────────────────────────────────────────────────────────────────────
def insert_document_and_chunks(
    conn,
    *,
    title: str,
    source: str,
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> dict:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    chunks = splitter.split_text(text)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents (title, source, content, metadata)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (
                title, source, text,
                json.dumps({"chunk_size": chunk_size,
                            "chunk_overlap": chunk_overlap,
                            "n_chunks": len(chunks)}),
            ),
        )
        document_id = cur.fetchone()[0]

        chunk_rows = [
            (document_id, i, chunk, json.dumps({"title": title, "source": source}))
            for i, chunk in enumerate(chunks)
        ]
        cur.executemany(
            """
            INSERT INTO document_chunks (document_id, chunk_index, content, metadata)
            VALUES (%s, %s, %s, %s)
            """,
            chunk_rows,
        )
    conn.commit()
    logger.info("Inserted document '%s' with %d chunks.", title, len(chunks))
    return {"document_id": document_id, "title": title, "source": source, "n_chunks": len(chunks)}


# ── CLI ────────────────────────────────────────────────────────────────────────
def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        description="Ingest a document into PostgreSQL and split it into chunks."
    )
    parser.add_argument("--title",         required=True,         help="Document title")
    parser.add_argument("--source",        default="manual",      help="Source label")
    parser.add_argument("--file",                                  help="Path to .txt or .pdf file")
    parser.add_argument("--text",                                  help="Inline text to ingest")
    parser.add_argument("--chunk-size",    type=int, default=500,  help="Chunk size (default: 500)")
    parser.add_argument("--chunk-overlap", type=int, default=50,   help="Chunk overlap (default: 50)")
    args = parser.parse_args()

    text = read_text(args.file, args.text)

    with get_conn() as conn:
        ensure_tables(conn)
        result = insert_document_and_chunks(
            conn,
            title=args.title,
            source=args.source,
            text=text,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
