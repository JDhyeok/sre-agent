"""Pydantic models for structured RCA output."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    NORMAL = "normal"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RiskLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# --- Prometheus Agent Output ---


class MetricAnomaly(BaseModel):
    metric_name: str
    current_value: float
    baseline_value: float
    deviation_percent: float
    severity: Severity
    description: str


class PrometheusReport(BaseModel):
    """Output from the Prometheus Agent."""

    timestamp: str
    anomalies: list[MetricAnomaly] = Field(default_factory=list)
    active_alerts: list[dict] = Field(default_factory=list)
    unhealthy_targets: list[dict] = Field(default_factory=list)
    summary: str


# --- Elasticsearch Agent Output ---


class LogPattern(BaseModel):
    template: str
    count: int
    percentage: float
    sample_messages: list[str] = Field(default_factory=list, max_length=3)


class ElasticsearchReport(BaseModel):
    """Output from the Elasticsearch Agent."""

    timestamp: str
    total_logs_analyzed: int
    error_count: int
    top_error_patterns: list[LogPattern] = Field(default_factory=list)
    timeline_summary: str
    affected_services: list[str] = Field(default_factory=list)
    summary: str


# --- SSH Agent Output ---


class HostDiagnostic(BaseModel):
    hostname: str
    command: str
    output: str
    observation: str


class SSHReport(BaseModel):
    """Output from the SSH Agent."""

    timestamp: str
    host_diagnostics: list[HostDiagnostic] = Field(default_factory=list)
    summary: str


# --- RCA Agent Output ---


class TimelineEvent(BaseModel):
    timestamp: str
    source: str = Field(description="prometheus | elasticsearch | ssh | alert")
    description: str


class RootCauseCandidate(BaseModel):
    cause: str
    confidence: Confidence
    evidence: list[str] = Field(min_length=1)
    causal_chain: str = Field(description="A -> B -> C chain of causation")


class RCAReport(BaseModel):
    """Output from the RCA Agent."""

    incident_summary: str
    timeline: list[TimelineEvent] = Field(default_factory=list)
    anomalies_identified: list[str] = Field(default_factory=list)
    correlations: list[str] = Field(default_factory=list)
    root_cause_candidates: list[RootCauseCandidate] = Field(min_length=1)
    data_gaps: list[str] = Field(
        default_factory=list,
        description="Missing data that would improve analysis confidence",
    )
    primary_root_cause: str


# --- Solution Agent Output ---


class Action(BaseModel):
    description: str
    estimated_time: str
    risk_level: RiskLevel
    commands_or_steps: list[str] = Field(default_factory=list)


class SolutionReport(BaseModel):
    """Output from the Solution Agent."""

    immediate_actions: list[Action] = Field(default_factory=list)
    short_term_actions: list[Action] = Field(default_factory=list)
    long_term_recommendations: list[Action] = Field(default_factory=list)
    summary: str


# --- Final Orchestrator Output ---


class IncidentAnalysis(BaseModel):
    """Complete incident analysis produced by the Orchestrator."""

    incident_context: str
    prometheus_report: PrometheusReport | None = None
    elasticsearch_report: ElasticsearchReport | None = None
    ssh_report: SSHReport | None = None
    rca_report: RCAReport | None = None
    solution_report: SolutionReport | None = None
    elapsed_seconds: float | None = None
