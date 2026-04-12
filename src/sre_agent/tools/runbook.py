"""Runbook loader — in-process tools for the Runbook Matcher agent.

Runbooks are Markdown files with YAML frontmatter, stored under
``src/sre_agent/runbooks/``. Each file describes ONE remediation procedure:

    ---
    name: redis-restart
    trigger: "free-form description of when to use this runbook"
    risk: low|medium|high|critical
    script: scripts/restart-redis.sh
    target_host_label: "service=redis"
    ---

    # When to use
    ...

    # What it does
    ...

The Runbook Matcher agent calls ``list_runbooks()`` to get a catalog
of available runbooks, then ``get_runbook(name)`` to fetch the full
content of one before deciding whether it matches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from strands import tool

logger = logging.getLogger(__name__)

_RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "runbooks"


@dataclass
class RunbookMetadata:
    name: str
    trigger: str
    risk: str
    script: str
    target_host_label: str
    file: str


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown file into (frontmatter dict, body)."""
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    raw_fm = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")

    try:
        fm = yaml.safe_load(raw_fm) or {}
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse runbook frontmatter: %s", exc)
        return {}, text

    if not isinstance(fm, dict):
        return {}, text

    return fm, body


def _load_one(path: Path) -> tuple[RunbookMetadata, str] | None:
    """Read and parse a single runbook file. Returns (metadata, body) or None."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cannot read runbook %s: %s", path, exc)
        return None

    fm, body = _parse_frontmatter(text)
    name = str(fm.get("name") or path.stem)

    metadata = RunbookMetadata(
        name=name,
        trigger=str(fm.get("trigger", "")),
        risk=str(fm.get("risk", "unknown")),
        script=str(fm.get("script", "")),
        target_host_label=str(fm.get("target_host_label", "")),
        file=path.name,
    )
    return metadata, body


def _iter_runbook_files() -> list[Path]:
    if not _RUNBOOK_DIR.is_dir():
        return []
    return sorted(p for p in _RUNBOOK_DIR.glob("*.md") if p.is_file())


def load_runbook_by_name(name: str) -> tuple[RunbookMetadata, str] | None:
    """Programmatic lookup used by the executor (not exposed as a tool)."""
    for path in _iter_runbook_files():
        loaded = _load_one(path)
        if loaded and loaded[0].name == name:
            return loaded
    return None


# ---------------------------------------------------------------------------
# Tools exposed to the Runbook Matcher agent
# ---------------------------------------------------------------------------


@tool
def list_runbooks() -> str:
    """List all available remediation runbooks with their trigger conditions.

    Returns a catalog of runbooks, one per line, including each runbook's
    name, risk level, and the free-form trigger description that explains
    when to use it. The matcher agent uses this list to find candidates;
    it must then call ``get_runbook(name)`` to read the full body before
    confirming a match.

    Returns:
        A human-readable catalog string. Empty string if no runbooks exist.
    """
    files = _iter_runbook_files()
    if not files:
        return "No runbooks available."

    entries: list[str] = [f"Found {len(files)} runbook(s):", ""]
    for path in files:
        loaded = _load_one(path)
        if not loaded:
            continue
        meta, _ = loaded
        entries.append(f"- name: {meta.name}")
        entries.append(f"  risk: {meta.risk}")
        entries.append(f"  trigger: {meta.trigger}")
        entries.append("")

    return "\n".join(entries).rstrip()


@tool
def get_runbook(name: str) -> str:
    """Fetch the full content of a single runbook by name.

    Use this AFTER ``list_runbooks()`` to read the detailed "When to use"
    and "What it does" sections of a candidate runbook before deciding
    whether it actually matches the current incident.

    Args:
        name: The runbook name (the ``name`` field from frontmatter).

    Returns:
        The full runbook text including frontmatter and body, or an
        error message if the runbook does not exist.
    """
    loaded = load_runbook_by_name(name)
    if not loaded:
        return f"Runbook '{name}' not found. Call list_runbooks() to see available runbooks."

    meta, body = loaded
    return (
        f"# Runbook: {meta.name}\n"
        f"- file: {meta.file}\n"
        f"- risk: {meta.risk}\n"
        f"- script: {meta.script}\n"
        f"- target_host_label: {meta.target_host_label}\n"
        f"- trigger: {meta.trigger}\n\n"
        f"---\n\n"
        f"{body}"
    )
