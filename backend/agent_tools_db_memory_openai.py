from __future__ import annotations
import json
import logging
import os
import re
import traceback
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

from agent_tools_db_fixed import DrugDiscoveryAgentTools as BaseTools, ToolResult

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

AGENT_PROFILE = {
    "name": "AI Drug Agent",
    "creator": "Paul Alemoh from the Dept. of Bioinformatics, UALR",
    "personality": "scientific, concise, drug discovery focused",
    "specialty": "AI-powered drug discovery for infectious diseases",
}

# ── Triggers that indicate a message needs RAG context ───────────────────────
_RAG_TRIGGERS = {
    "result", "results", "report", "analysis", "accuracy", "model",
    "confusion", "precision", "recall", "f1", "auc", "roc",
    "compound", "molecule", "smiles", "target", "ic50", "pic50",
    "bioactivity", "descriptor", "lipinski", "active", "inactive",
    "classification", "dataset", "data", "paper", "study", "document",
    # AlphaFold / structure
    "alphafold", "structure", "plddt", "protein", "pdb", "folding",
    "docking", "binding", "pocket", "residue", "uniprot",
}

# ── Disease keyword list (expandable) ────────────────────────────────────────
_KNOWN_DISEASES = [
    "diphtheria", "cancer", "diabetes", "alzheimer", "malaria", "tuberculosis",
    "hiv", "covid", "influenza", "hepatitis", "cholera", "typhoid", "ebola",
    "dengue", "leishmania", "trypanosoma", "parkinson", "hypertension",
]


class DrugDiscoveryAgentTools(BaseTools):
    """
    Extends DB-backed workflow tools with conversation memory and OpenRouter LLM chat.

    Improvements over original:
    - RAG is only triggered when the message content suggests it is needed
      (avoids embedding lookups on every single greeting/identity question).
    - Disease inference expanded beyond 4 hardcoded diseases.
    - Lazy RAG import — document_rag is only imported once and only when needed.
    - Structured logging instead of bare print().
    - _system_prompt indentation bug fixed (from old file).
    - Loose class-body statements from old file removed.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.db_ready:
            self.init_conversation_db()

        # OpenRouter configuration.
        # We keep self.openai_model as the variable name so the rest of the framework stays unchanged.
        # The OpenAI Python SDK is used only as an OpenAI-compatible client; base_url sends requests to OpenRouter.
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.openai_model = (
            os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
            or "deepseek/deepseek-v4-flash"
        ).strip()

        self.client = (
            OpenAI(
                api_key=self.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:8000"),
                    "X-Title": os.getenv("OPENROUTER_APP_NAME", "AI Drug Agent"),
                },
            )
            if OpenAI is not None and self.openrouter_api_key
            else None
        )
        self._rag_module = None   # lazy-loaded on first use

        logger.info("OpenRouter key present: %s", bool(self.openrouter_api_key))
        logger.info("OpenRouter client ready: %s", self.client is not None)
        logger.info("OpenRouter chat model: %s", self.openai_model)

    # ── Conversation DB ────────────────────────────────────────────────────────
    def init_conversation_db(self) -> ToolResult:
        if not self.db_ready:
            return ToolResult(ok=False, step="init_conversation_db",
                              message="Database not available.")
        ddl = """
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            session_id TEXT NOT NULL,
            user_id TEXT,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
        CREATE INDEX IF NOT EXISTS idx_conv_created  ON conversations(created_at);
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(ddl)
                conn.commit()
            return ToolResult(ok=True, step="init_conversation_db",
                              message="Conversation table ready.")
        except Exception as exc:
            return ToolResult(ok=False, step="init_conversation_db",
                              message="Conversation table init failed.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def save_message(
        self,
        session_id: str,
        role: str,
        message: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        if not self.db_ready:
            return ToolResult(ok=False, step="save_message", message="Database not available.")
        sql = """
            INSERT INTO conversations (session_id, user_id, role, message, metadata)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (
                        session_id, user_id, role, message,
                        self._json_value(metadata or {}),
                    ))
                    row_id = cur.fetchone()[0]
                conn.commit()
            return ToolResult(ok=True, step="save_message",
                              message="Saved.", data={"id": row_id})
        except Exception as exc:
            return ToolResult(ok=False, step="save_message",
                              message="Could not save message.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def get_conversation_history(self, session_id: str, limit: int = 20) -> ToolResult:
        if not self.db_ready:
            return ToolResult(ok=False, step="get_conversation_history",
                              message="Database not available.")
        sql = """
            SELECT role, message, created_at
            FROM conversations
            WHERE session_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (session_id, limit))
                    rows = cur.fetchall()
            history = [
                {"role": role, "message": msg, "created_at": str(ts)}
                for role, msg, ts in reversed(rows)
            ]
            return ToolResult(
                ok=True, step="get_conversation_history",
                message="History retrieved.",
                data={"session_id": session_id, "history": history, "count": len(history)},
            )
        except Exception as exc:
            return ToolResult(ok=False, step="get_conversation_history",
                              message="Could not retrieve history.",
                              error=f"{exc}\n{traceback.format_exc()}")

    def clear_conversation(self, session_id: str) -> ToolResult:
        if not self.db_ready:
            return ToolResult(ok=False, step="clear_conversation",
                              message="Database not available.")
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM conversations WHERE session_id = %s", (session_id,))
                conn.commit()
            return ToolResult(ok=True, step="clear_conversation",
                              message="Conversation cleared.")
        except Exception as exc:
            return ToolResult(ok=False, step="clear_conversation",
                              message="Could not clear conversation.",
                              error=f"{exc}\n{traceback.format_exc()}")

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _needs_rag(self, message: str) -> bool:
        """Return True only if the message likely benefits from document context."""
        words = set(re.findall(r"[a-z]+", message.lower()))
        return bool(words & _RAG_TRIGGERS)

    def _get_rag(self):
        """Lazy-load document_rag module once."""
        if self._rag_module is None:
            try:
                import document_rag as rag
                self._rag_module = rag
            except Exception as exc:
                logger.warning("Could not import document_rag: %s", exc)
                self._rag_module = False   # mark as failed so we don't retry
        return self._rag_module if self._rag_module else None

    def _infer_disease(self, message: str) -> Optional[str]:
        """
        Extract a disease name from a message.
        Checks known list first, then falls back to regex pattern matching.
        """
        lower = message.lower()
        for disease in _KNOWN_DISEASES:
            if disease in lower:
                return disease
        # "last <disease> run" pattern
        match = re.search(r"last\s+([a-zA-Z0-9_-]+)\s+run", lower)
        if match:
            return match.group(1)
        # "run for <disease>" pattern
        match = re.search(r"run\s+for\s+([a-zA-Z0-9_-]+)", lower)
        if match:
            return match.group(1)
        return None

    def _recent_user_mentions(self, history: List[Dict[str, Any]], keyword: str) -> List[str]:
        key = keyword.lower()
        return [
            item["message"] for item in history
            if item.get("role") == "user" and key in item.get("message", "").lower()
        ]

    def _system_prompt(self) -> str:
        return (
            f"You are {AGENT_PROFILE['name']}, created by {AGENT_PROFILE['creator']}. "
            f"You specialize in {AGENT_PROFILE['specialty']}. "
            f"Your personality is {AGENT_PROFILE['personality']}. "
            "You are NOT a general chatbot. You are a full AI drug discovery agent. "
            "You must use structured context, database results, workflow outputs, RAG documents, and conversation history when available. "
            "You can interpret ChEMBL targets, analyze bioactivity values such as IC50 and pIC50, rank compounds, explain SMILES, "
            "evaluate ML results such as accuracy, F1, recall, precision, ROC-AUC, and confusion matrices, summarize workflow outputs, "
            "analyze AlphaFold structures, interpret pLDDT confidence, and connect structure information to compounds and ML results. "
            "If real data is provided in the context, use that data directly. Do not invent placeholder compounds, fake ChEMBL IDs, or fake SMILES. "
            "If the needed file/database context is missing, say exactly what is missing and which run, table, endpoint, or file should be checked. "
            "When asked who you are or who created you, answer exactly: "
            "'I am AI Drug Agent, created by Paul Alemoh from the Dept. of Bioinformatics, UALR.' "
            "Answer clearly, concisely, and scientifically."
        )

    def _structured_context(
        self, session_id: str, message: str, history: list
    ) -> str:
        blocks = []
        disease = self._infer_disease(message)
        lower = message.lower()

        if disease and self.db_ready:

            # always include run summary
            latest = self.get_run_summary(disease)
            if latest.ok and latest.data:
                d = latest.data
                blocks.append(
                    f"Latest {disease} run:\n"
                    f"  run_id={d.get('run_id')}, target={d.get('selected_target')},\n"
                    f"  status={d.get('status')}, compounds={d.get('n_compounds')},\n"
                    f"  bioactivity_records={d.get('n_bioactivity')}, targets={d.get('n_targets')}"
                )

            # if asking about compounds / SMILES / molecules
            if any(w in lower for w in ["compound", "smiles", "molecule", "active", "inactive", "pic50"]):
                activity = None
                if "active" in lower and "inactive" not in lower:
                    activity = "active"
                elif "inactive" in lower:
                    activity = "inactive"

                cmp = self.get_compounds(disease, activity_class=activity, limit=10)
                if cmp.ok and cmp.data and cmp.data.get("compounds"):
                    rows = cmp.data["compounds"]
                    lines = [f"  {r['molecule_chembl_id']} | class={r['class']} | "
                             f"pIC50={r['pic50']} | smiles={r['smiles']}"
                             for r in rows[:10]]
                    blocks.append(
                        f"Top compounds for {disease}"
                        + (f" (class={activity})" if activity else "")
                        + ":\n" + "\n".join(lines)
                    )

            # if asking about targets
            if any(w in lower for w in ["target", "protein", "chembl", "receptor"]):
                tgt = self.get_targets(disease)
                if tgt.ok and tgt.data and tgt.data.get("targets"):
                    rows = tgt.data["targets"]
                    lines = [f"  {r['target_chembl_id']} | {r['pref_name']} | score={r['score']}"
                             for r in rows[:5]]
                    blocks.append(f"Targets for {disease}:\n" + "\n".join(lines))

            # if asking about ML / model / accuracy / results
            if any(w in lower for w in ["ml", "model", "accuracy", "f1", "recall", "precision",
                                         "random forest", "svm", "result", "performance"]):
                ml = self.get_ml_results(disease)
                if ml.ok and ml.data and ml.data.get("results"):
                    rows = ml.data["results"]
                    lines = [
                        f"  {r['model']} | acc={r['accuracy']:.3f} | f1={r['f1']:.3f} | "
                        f"cv={r['cv_mean']:.3f}±{r['cv_std']:.3f}"
                        + (" ← BEST" if r.get("best") else "")
                        for r in rows
                    ]
                    blocks.append(f"ML results for {disease}:\n" + "\n".join(lines))

            # if asking about AlphaFold / protein structure / docking
            if any(w in lower for w in ["alphafold", "structure", "plddt", "pdb",
                                         "docking", "binding", "pocket", "fold"]):
                af = self.get_alphafold_structure(disease)
                if af.ok and af.data:
                    d = af.data
                    blocks.append(
                        f"AlphaFold structure for {disease}:\n"
                        f"  UniProt={d.get('uniprot_id')} | protein={d.get('protein_name')}\n"
                        f"  mean_pLDDT={d.get('mean_plddt')} | "
                        f"very_high={d.get('pct_very_high')}% | "
                        f"confident={d.get('pct_confident')}% | "
                        f"confident_regions={d.get('n_confident_regions')}\n"
                        f"  PDB available: {bool(d.get('pdb_path'))} | "
                        f"filtered_PDB: {bool(d.get('filtered_pdb_path'))}"
                    )

        # if asking about all runs / overview
        if any(w in lower for w in ["all runs", "list runs", "what diseases", "all diseases",
                                     "ran so far", "history"]):
            all_runs = self.list_all_runs()
            if all_runs.ok and all_runs.data and all_runs.data.get("runs"):
                rows = all_runs.data["runs"]
                lines = [
                    f"  {r['disease_name']} | target={r['selected_target']} | "
                    f"status={r['status']} | compounds={r['n_compounds']} | {r['created_at'][:10]}"
                    for r in rows[:10]
                ]
                blocks.append("All stored runs:\n" + "\n".join(lines))

        # recent conversation
        if history:
            rendered = "\n".join(
                f"{item['role']}: {item['message']}"
                for item in history[-8:]
            )
            blocks.append(f"Recent conversation:\n{rendered}")

        return "\n\n".join(blocks).strip()

    # ── Fallback (no LLM) ──────────────────────────────────────────────────────
    def _fallback_chat(
        self,
        session_id: str,
        message: str,
        user_id: Optional[str],
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        lower = message.lower().strip()
        payload: Dict[str, Any] = {"session_id": session_id, "mode": "fallback"}

        if any(p in lower for p in ["your name", "who are you"]):
            text = (
                f"I am {AGENT_PROFILE['name']}, created by {AGENT_PROFILE['creator']}. "
                f"I specialize in {AGENT_PROFILE['specialty']}."
            )
        elif "who created you" in lower:
            text = f"I was created by {AGENT_PROFILE['creator']}."
        elif any(p in lower for p in ["what did i ask", "what did we discuss", "past conversation"]):
            disease = self._infer_disease(message)
            if disease:
                mentions = self._recent_user_mentions(history, disease)
                text = (
                    "Earlier you asked: " + " | ".join(mentions[-5:])
                    if mentions
                    else f"No earlier questions about {disease} in this session."
                )
            else:
                prior = [item["message"] for item in history if item.get("role") == "user"]
                text = (
                    "Earlier you asked: " + " | ".join(prior[-5:])
                    if prior
                    else "No earlier user messages in this session."
                )
        elif "last" in lower and "run" in lower:
            disease = self._infer_disease(message) or "diphtheria"
            latest = self.get_run_summary(disease)
            if latest.ok and latest.data:
                d = latest.data
                text = (
                    f"Latest {disease} run: run_id={d.get('run_id')}, "
                    f"target={d.get('selected_target')}, status={d.get('status')}."
                )
                payload["run_summary"] = d
            else:
                text = f"No stored run found for '{disease}'."
        elif "conversation history" in lower or "show history" in lower:
            text = "Here is the recent conversation history."
            payload["history"] = history
        elif any(w in lower for w in ["alphafold", "structure", "plddt", "pdb", "binding pocket"]):
            disease = self._infer_disease(message)
            af = self.get_alphafold_structure(disease) if disease else None
            if af and af.ok and af.data:
                d = af.data
                text = (
                    f"AlphaFold structure on record for {disease}: "
                    f"UniProt={d.get('uniprot_id')}, protein={d.get('protein_name')}, "
                    f"mean pLDDT={d.get('mean_plddt')}, "
                    f"{d.get('pct_confident')}% high-confidence residues, "
                    f"{d.get('n_confident_regions')} druggable regions."
                )
            else:
                name = disease or "this disease"
                text = (
                    f"No AlphaFold structure found for '{name}'. "
                    "Call POST /alphafold/fetch with the disease name to download one."
                )
        elif not self.openrouter_api_key:
            text = "OPENROUTER_API_KEY is not set. LLM chat is inactive."
        elif OpenAI is None:
            text = "Install the openai package: pip install openai"
        else:
            text = "LLM unavailable — your message has been saved to memory."

        self.save_message(session_id, "assistant", text, user_id=user_id, metadata=payload)
        payload.update({"ok": True, "message": text})
        return payload

    # ── Main chat entry point ──────────────────────────────────────────────────
    def chat(
        self, session_id: str, message: str, user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        save_user = self.save_message(session_id, "user", message, user_id=user_id)
        history_result = self.get_conversation_history(session_id, limit=20)
        history = (history_result.data or {}).get("history", []) if history_result.ok else []
        prior_history = history[:-1] if history else []

        if self.client is None:
            payload = self._fallback_chat(session_id, message, user_id, prior_history)
            payload["saved_user"] = save_user.ok
            return payload

        try:
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": self._system_prompt()}
            ]

            context = self._structured_context(session_id, message, prior_history)
            if context:
                messages.append({"role": "system", "content": f"Context:\n{context}"})

            # Only invoke RAG when the message content warrants it
            if self._needs_rag(message):
                rag = self._get_rag()
                if rag:
                    try:
                        doc_context = rag.retrieve_context(message, k=4)
                        if doc_context:
                            messages.append({
                                "role": "system",
                                "content": (
                                    "Relevant document extracts (interpret numerics as "
                                    "structured results — metrics, confusion matrices, tables):\n"
                                    + doc_context
                                ),
                            })
                    except Exception as rag_exc:
                        logger.warning("RAG retrieval failed: %s", rag_exc)

            for item in prior_history[-12:]:
                role = item.get("role")
                if role in {"user", "assistant"}:
                    messages.append({"role": role, "content": item.get("message", "")})
            messages.append({"role": "user", "content": message})

            response = self.client.chat.completions.create(
                model=self.openai_model,
                messages=messages,
                temperature=0.3,
            )
            reply = (response.choices[0].message.content or "").strip() or "No response generated."

            save_asst = self.save_message(
                session_id, "assistant", reply, user_id=user_id,
                metadata={"model": self.openai_model, "used_openrouter": True},
            )
            return {
                "ok": True,
                "message": reply,
                "session_id": session_id,
                "saved_user": save_user.ok,
                "saved_assistant": save_asst.ok,
                "model": self.openai_model,
            }

        except Exception as exc:
            logger.error("LLM chat error: %s", exc)
            fallback = self._fallback_chat(session_id, message, user_id, prior_history)
            fallback["saved_user"] = save_user.ok
            fallback["llm_error"] = str(exc)
            return fallback
