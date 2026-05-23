"""
document_rag.py — RAG layer using PGVector + OpenAI embeddings.

Key fix: vectorstore is NOT initialized at module import time.
Original code crashed the entire app on startup when the DB was unavailable
because PGVector() was called at module level.
Now it is lazy-initialized on first use.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()
logger = logging.getLogger(__name__)

COLLECTION = "drug_documents"

# ── DB URL ─────────────────────────────────────────────────────────────────────
def _db_url() -> str:
    return (
        f"postgresql+psycopg://"
        f"{os.getenv('PGUSER')}:{os.getenv('PGPASSWORD')}@"
        f"{os.getenv('PGHOST', 'localhost')}:{os.getenv('PGPORT', '5432')}/"
        f"{os.getenv('PGDATABASE', 'drugdb')}"
    )

# ── Lazy vectorstore singleton ─────────────────────────────────────────────────
_vectorstore: Optional[PGVector] = None

def _get_vectorstore() -> Optional[PGVector]:
    """Return vectorstore, initializing once on first call. Returns None on failure."""
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore
    try:
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        _vectorstore = PGVector(
            embeddings=embeddings,
            collection_name=COLLECTION,
            connection=_db_url(),
            use_jsonb=True,
        )
        logger.info("PGVector vectorstore initialized.")
    except Exception as exc:
        logger.warning("Could not initialize PGVector: %s", exc)
        _vectorstore = None
    return _vectorstore


# ── Ingest ─────────────────────────────────────────────────────────────────────
def ingest_text(title: str, text: str, source: str = "manual") -> int:
    """
    Split text into chunks and store in the vectorstore.
    Returns the number of chunks stored, or 0 on failure.
    """
    vs = _get_vectorstore()
    if vs is None:
        logger.error("Vectorstore unavailable — cannot ingest.")
        return 0

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    chunks = splitter.split_text(text)
    docs: List[Document] = [
        Document(page_content=chunk, metadata={"title": title, "source": source})
        for chunk in chunks
    ]
    vs.add_documents(docs)
    logger.info("Ingested %d chunks for '%s'.", len(docs), title)
    return len(docs)


# ── Retrieve ───────────────────────────────────────────────────────────────────
def retrieve_context(query: str, k: int = 4) -> str:
    """
    Return top-k relevant document chunks as a formatted string.
    Returns empty string if vectorstore is unavailable or no results found.
    """
    vs = _get_vectorstore()
    if vs is None:
        return ""
    try:
        docs = vs.similarity_search(query, k=k)
        return "\n\n".join(
            f"[{d.metadata.get('title', 'untitled')}]\n{d.page_content}"
            for d in docs
        )
    except Exception as exc:
        logger.warning("RAG retrieval error: %s", exc)
        return ""


# ── Answer with RAG ────────────────────────────────────────────────────────────
def answer_with_rag(query: str, model: str = "gpt-4.1-mini") -> str:
    """Answer a query using retrieved context + LLM."""
    context = retrieve_context(query, k=4)
    llm = ChatOpenAI(model=model, temperature=0)
    prompt = (
        "Answer using the context below when relevant.\n\n"
        f"Context:\n{context}\n\n"
        f"Question:\n{query}"
    )
    return llm.invoke(prompt).content
