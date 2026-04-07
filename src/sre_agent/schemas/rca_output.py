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


# --- Data Collector Agent Output ---


class MetricAnomaly(BaseModel):
    metric_name: str
    current_value: float
    baseline_value: float
    deviation_percent: float
    severity: Severity
    description: str


class LogPattern(BaseModel):
    template: str
    count: int
    percentage: float
    sample_messages: list[str] = Field(default_factory=list, max_length=3)


class DataCollectorReport(BaseModel):
    """Output from the unified Data Collector Agent."""

    timestamp: str
    investigation_layers: list[str] = Field(
        default_factory=list,
        description="Which L1-L6 layers were investigated",
    )
    metric_anomalies: list[MetricAnomaly] = Field(default_factory=list)
    active_alerts: list[dict] = Field(default_factory=list)
    unhealthy_targets: list[dict] = Field(default_factory=list)
    error_count: int = 0
    top_error_patterns: list[LogPattern] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    topology_context: list[dict] = Field(
        default_factory=list,
        description="Service dependencies and CI relationships from CMDB",
    )
    key_findings: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
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


# --- RCA Agent Output (5-Phase Framework) ---


class TriageResult(BaseModel):
    symptom_type: str = Field(description="service_error | performance | availability | resource | data")
    severity: Severity
    blast_radius: str
    affected_services: list[str] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    timestamp: str
    source: str = Field(description="prometheus | elasticsearch | ssh | alert | cmdb")
    event: str
    significance: str = Field(default="context", description="leading | lagging | context")


class CorrelationFinding(BaseModel):
    finding: str
    sources: list[str] = Field(default_factory=list)
    strength: str = Field(default="moderate", description="strong | moderate | weak")


class RootCauseCandidate(BaseModel):
    cause: str
    category: str = Field(description="deployment | resource | dependency | infrastructure | traffic | operational")
    confidence: Confidence
    evidence: list[str] = Field(min_length=1)
    causal_chain: str = Field(description="A -> B -> C -> symptom")


class VerificationResult(BaseModel):
    explains_all_symptoms: bool = True
    counter_evidence: list[str] = Field(default_factory=list)
    predictions: list[str] = Field(default_factory=list)
    confidence_statement: str = ""


class RCAReport(BaseModel):
    """Output from the RCA Agent using 5-Phase Framework."""

    triage: TriageResult | None = None
    timeline: list[TimelineEvent] = Field(default_factory=list)
    correlations: list[CorrelationFinding] = Field(default_factory=list)
    five_whys: list[str] = Field(default_factory=list)
    root_cause_candidates: list[RootCauseCandidate] = Field(min_length=1)
    verification: VerificationResult | None = None
    data_gaps: list[str] = Field(default_factory=list)
    primary_root_cause: str
    recommended_next_steps: list[str] = Field(default_factory=list)


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
    data_collector_report: DataCollectorReport | None = None
    ssh_report: SSHReport | None = None
    rca_report: RCAReport | None = None
    solution_report: SolutionReport | None = None
    elapsed_seconds: float | None = None
