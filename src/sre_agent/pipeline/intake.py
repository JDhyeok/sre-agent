"""Intake module — webhook receiver, dedup, severity routing, alert grouping.

Deterministic code logic (no LLM). Converts raw Alertmanager / generic webhook
payloads into ``IncidentRequest`` objects ready for the Analyzer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sre_agent.config import IntakeConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class AnalysisLevel(str, Enum):
    FULL_ANALYSIS = "full_analysis"
    LIGHTWEIGHT = "lightweight"
    LOG_ONLY = "log_only"
    SUMMARY_ONLY = "summary_only"


@dataclass
class AlertItem:
    alertname: str
    severity: str
    status: str
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: str = ""
    ends_at: str = ""
    generator_url: str = ""
    fingerprint: str = ""


@dataclass
class IncidentRequest:
    """A grouped set of alerts ready for analysis."""

    incident_id: str
    alerts: list[AlertItem]
    analysis_level: AnalysisLevel
    group_key: str
    received_at: float = field(default_factory=time.time)

    @property
    def primary_alertname(self) -> str:
        return self.alerts[0].alertname if self.alerts else "unknown"

    @property
    def primary_severity(self) -> str:
        order = {"critical": 0, "warning": 1, "info": 2}
        return min(
            (a.severity for a in self.alerts),
            key=lambda s: order.get(s, 99),
            default="info",
        )

    def format_context(self) -> str:
        """Build a human-readable incident context string for the Analyzer."""
        lines = [
            f"Incident {self.incident_id}",
            f"Analysis Level: {self.analysis_level.value}",
            f"Severity: {self.primary_severity}",
            f"Alert Count: {len(self.alerts)}",
            "",
        ]
        for i, alert in enumerate(self.alerts, 1):
            lines.append(f"--- Alert {i} ---")
            lines.append(f"  Name: {alert.alertname}")
            lines.append(f"  Severity: {alert.severity}")
            lines.append(f"  Status: {alert.status}")
            lines.append(f"  Started: {alert.starts_at}")
            lines.append(f"  Labels: {json.dumps(alert.labels)}")
            if alert.annotations.get("summary"):
                lines.append(f"  Summary: {alert.annotations['summary']}")
            if alert.annotations.get("description"):
                lines.append(f"  Description: {alert.annotations['description']}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

class AlertDeduplicator:
    """Skip duplicate alerts within a configurable time window."""

    def __init__(self, window_minutes: int = 5) -> None:
        self._window = window_minutes * 60
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(alert: AlertItem) -> str:
        raw = f"{alert.alertname}|{alert.labels.get('service', '')}|{alert.labels.get('instance', '')}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def is_duplicate(self, alert: AlertItem) -> bool:
        key = self._key(alert)
        now = time.time()
        with self._lock:
            self._evict(now)
            if key in self._seen:
                logger.debug("Dedup: skipping duplicate alert %s (%s)", alert.alertname, key)
                return True
            self._seen[key] = now
            return False

    def _evict(self, now: float) -> None:
        expired = [k for k, ts in self._seen.items() if now - ts > self._window]
        for k in expired:
            del self._seen[k]


# ---------------------------------------------------------------------------
# Severity router
# ---------------------------------------------------------------------------

def route_severity(severity: str, routing: dict[str, str]) -> AnalysisLevel:
    """Map alert severity to an analysis level using the config routing table."""
    level_str = routing.get(severity, routing.get("warning", "lightweight"))
    try:
        return AnalysisLevel(level_str)
    except ValueError:
        return AnalysisLevel.LIGHTWEIGHT


# ---------------------------------------------------------------------------
# Alert grouper
# ---------------------------------------------------------------------------

class AlertGrouper:
    """Collect related alerts within a time window into a single IncidentRequest."""

    def __init__(self, window_seconds: int = 60) -> None:
        self._window = window_seconds
        self._pending: dict[str, list[AlertItem]] = {}
        self._timestamps: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _group_key(alert: AlertItem) -> str:
        service = alert.labels.get("service", alert.labels.get("job", "default"))
        return f"grp-{service}"

    def add(self, alert: AlertItem) -> str:
        """Add an alert and return its group key."""
        key = self._group_key(alert)
        now = time.time()
        with self._lock:
            if key not in self._pending:
                self._pending[key] = []
                self._timestamps[key] = now
            self._pending[key].append(alert)
        return key

    def flush_ready(self) -> list[tuple[str, list[AlertItem]]]:
        """Return groups whose window has elapsed, removing them from pending."""
        now = time.time()
        ready: list[tuple[str, list[AlertItem]]] = []
        with self._lock:
            expired_keys = [
                k for k, ts in self._timestamps.items()
                if now - ts >= self._window
            ]
            for k in expired_keys:
                ready.append((k, self._pending.pop(k)))
                del self._timestamps[k]
        return ready

    def flush_all(self) -> list[tuple[str, list[AlertItem]]]:
        """Force-flush all pending groups (used for immediate processing)."""
        with self._lock:
            ready = list(self._pending.items())
            self._pending.clear()
            self._timestamps.clear()
        return ready


# ---------------------------------------------------------------------------
# Intake processor — ties it all together
# ---------------------------------------------------------------------------

_incident_counter = 0
_counter_lock = threading.Lock()


def _next_incident_id() -> str:
    global _incident_counter
    with _counter_lock:
        _incident_counter += 1
        return f"INC-{int(time.time())}-{_incident_counter:04d}"


class IntakeProcessor:
    """Stateful intake pipeline: dedup -> severity routing -> grouping."""

    def __init__(self, config: IntakeConfig) -> None:
        self.config = config
        self.dedup = AlertDeduplicator(window_minutes=config.dedup_window_minutes)
        self.grouper = AlertGrouper(window_seconds=config.group_window_seconds)

    def process_alertmanager_payload(self, payload: dict[str, Any]) -> list[IncidentRequest]:
        """Process a raw Alertmanager webhook payload.

        Returns a list of IncidentRequests (may be empty if all alerts are deduped,
        or delayed if the grouping window hasn't elapsed yet).
        """
        alerts_raw = payload.get("alerts", [])
        for raw in alerts_raw:
            labels = raw.get("labels", {})
            alert = AlertItem(
                alertname=labels.get("alertname", "unknown"),
                severity=labels.get("severity", "warning"),
                status=raw.get("status", "firing"),
                labels=labels,
                annotations=raw.get("annotations", {}),
                starts_at=raw.get("startsAt", ""),
                ends_at=raw.get("endsAt", ""),
                generator_url=raw.get("generatorURL", ""),
                fingerprint=raw.get("fingerprint", ""),
            )

            if self.dedup.is_duplicate(alert):
                continue

            self.grouper.add(alert)

        return self._build_requests_immediate()

    def process_generic_payload(self, payload: dict[str, Any]) -> list[IncidentRequest]:
        """Process a generic webhook payload with custom fields."""
        alert = AlertItem(
            alertname=payload.get("alertname", payload.get("title", "generic-alert")),
            severity=payload.get("severity", "warning"),
            status=payload.get("status", "firing"),
            labels=payload.get("labels", {}),
            annotations=payload.get("annotations", {"summary": payload.get("message", "")}),
        )

        if self.dedup.is_duplicate(alert):
            return []

        self.grouper.add(alert)
        return self._build_requests_immediate()

    def _build_requests_immediate(self) -> list[IncidentRequest]:
        """Flush all pending groups immediately (no waiting for grouping window).

        For real-time responsiveness. The grouping window primarily prevents
        duplicate analysis of the same alert burst rather than delaying response.
        """
        groups = self.grouper.flush_all()
        requests: list[IncidentRequest] = []
        for group_key, alerts in groups:
            severity = min(
                (a.severity for a in alerts),
                key=lambda s: {"critical": 0, "warning": 1, "info": 2}.get(s, 99),
                default="warning",
            )
            level = route_severity(severity, self.config.severity_routing)
            requests.append(IncidentRequest(
                incident_id=_next_incident_id(),
                alerts=alerts,
                analysis_level=level,
                group_key=group_key,
            ))
        return requests
