"""Incident Knowledge Base for storing and retrieving past RCA reports.

Uses a local JSON-based store for simplicity. Can be extended to use
vector databases (OpenSearch, Pinecone) for semantic search.
"""

from __future__ import annotations

import json
import logging
import os
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_KB_PATH = os.environ.get("SRE_AGENT_KB_PATH", "data/incident_kb.json")


class IncidentKB:
    """Simple file-based incident knowledge base with keyword similarity search."""

    def __init__(self, path: str | Path = DEFAULT_KB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._incidents: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            with open(self.path) as f:
                self._incidents = json.load(f)
            logger.info("Loaded %d incidents from KB", len(self._incidents))
        else:
            self._incidents = []

    def _save(self) -> None:
        with open(self.path, "w") as f:
            json.dump(self._incidents, f, indent=2, ensure_ascii=False)

    def store(self, incident: dict[str, Any]) -> str:
        """Store a completed incident analysis in the KB."""
        incident_id = f"INC-{int(time.time())}"
        record = {
            "id": incident_id,
            "stored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **incident,
        }
        self._incidents.append(record)
        self._save()
        logger.info("Stored incident %s", incident_id)
        return incident_id

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Search for similar past incidents using keyword similarity.

        Uses a combination of exact keyword matching and sequence similarity
        on incident summaries and root cause descriptions.
        """
        if not self._incidents:
            return []

        scored: list[tuple[float, dict]] = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for incident in self._incidents:
            searchable = " ".join([
                incident.get("incident_context", ""),
                incident.get("primary_root_cause", ""),
                incident.get("incident_summary", ""),
                incident.get("summary", ""),
            ]).lower()

            word_overlap = len(query_words & set(searchable.split())) / max(len(query_words), 1)
            seq_ratio = SequenceMatcher(None, query_lower[:200], searchable[:200]).ratio()
            score = (word_overlap * 0.6) + (seq_ratio * 0.4)

            scored.append((score, incident))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:top_k] if item[0] > 0.1]

    def list_recent(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the N most recent incidents."""
        return list(reversed(self._incidents[-n:]))
