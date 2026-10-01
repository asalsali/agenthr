"""AgentHR data models -- workforce management for AI agents.

Pydantic v2 models covering agent lifecycle, trust, health monitoring,
spawn governance, and operational telemetry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums as Literal unions (lightweight, no extra enum module)
# ---------------------------------------------------------------------------

AgentStatus = Literal["onboarding", "active", "blocked", "shutdown", "archived"]
AgentPhase = Literal["genesis", "research", "execution", "output", "shutdown"]
Framework = Literal["langgraph", "crewai", "claude-code", "custom"]
MandateType = Literal["research", "implementation", "audit", "review", "synthesis"]
Momentum = Literal["converging", "diverging", "neutral"]
PressureLevel = Literal["green", "yellow", "orange", "red"]
Impact = Literal["high", "medium", "low"]
Confidence = Literal["high", "medium", "low"]
DissentOutcome = Literal["rejected", "deferred", "superseded"]
Severity = Literal["incidental", "systematic"]
SignalType = Literal[
    "territory_drift",
    "token_hemorrhage",
    "stuck_loop",
    "role_drift",
    "output_contradiction",
]
SignalSeverity = Literal["warning", "escalation"]


# ---------------------------------------------------------------------------
# Nested / supporting models
# ---------------------------------------------------------------------------


class Gap(BaseModel):
    """An identified area of uncertainty from an agent's work."""

    domain: str
    description: str
    impact: Impact
    suggested_action: str


class DecisionNode(BaseModel):
    """A traceable decision node within an exit report's decision graph."""

    id: str = Field(
        ..., description="Globally unique, format d-<agentId>-<sequence>"
    )
    summary: str
    reasoning: str
    depends_on: list[str] = Field(
        default_factory=list,
        description="Decision IDs this decision required as input",
    )
    informed_by: list[str] = Field(
        default_factory=list,
        description="Decision IDs that influenced but did not gate this decision",
    )
    confidence: Confidence
    tags: list[str] = Field(default_factory=list)
    made_at: datetime


class DissentRecord(BaseModel):
    """A preserved minority position -- future resource, not discarded noise."""

    position: str
    reasoning: str
    outcome: DissentOutcome
    context: str


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------


class Agent(BaseModel):
    """The central agent record -- an AI worker in the system."""

    model_config = {"populate_by_name": True}

    id: UUID = Field(default_factory=uuid4)
    type: str = Field(..., description='Agent archetype, e.g. "analyst", "writer"')
    name: str
    parent_id: UUID | None = None
    parent_ids: list[UUID] = Field(
        default_factory=list,
        description="Dual-parent lineage for synthesized agents",
    )
    generation: int = Field(default=0, ge=0, description="Depth in genealogy tree")
    framework: Framework = "custom"
    definition_hash: str | None = Field(
        default=None, description="SHA-256 of the agent type definition"
    )
    team_id: UUID | None = None
    territory: list[str] = Field(
        default_factory=list,
        description="File globs, API surfaces, or conceptual domains owned",
    )
    mandate_type: MandateType | None = None
    mandate: str
    mandate_echo: str | None = Field(
        default=None,
        max_length=200,
        description="Agent's restatement of its mandate for alignment check",
    )
    status: AgentStatus = "onboarding"
    phase: AgentPhase = "genesis"
    trust_level: int = Field(default=0, ge=0, le=3)
    born_at: datetime = Field(default_factory=datetime.utcnow)
    archived_at: datetime | None = None
    tokens_consumed: int = Field(default=0, ge=0)
    metadata: dict[str, Any] | None = None


class Heartbeat(BaseModel):
    """Semantic heartbeat -- structured record of an agent's last meaningful action."""

    agent_id: UUID
    last_action: str = Field(..., max_length=120)
    phase: AgentPhase
    action_count: int = Field(default=0, ge=0)
    momentum: Momentum = "neutral"
    tool_breakdown: dict[str, int] = Field(default_factory=dict)
    stale_since: datetime | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ExitReport(BaseModel):
    """Post-mandate report -- the agent's final handoff to successors."""

    id: UUID = Field(default_factory=uuid4)
    agent_id: UUID
    mandate_completed: bool
    key_findings: list[str] = Field(default_factory=list)
    what_worked: str = ""
    what_failed: str = ""
    recommendations: str = ""
    gaps: list[Gap] = Field(default_factory=list)
    decisions: list[DecisionNode] = Field(default_factory=list)
    dissent: list[DissentRecord] = Field(default_factory=list)
    contrarian: str | None = None
    tokens_consumed: int = Field(default=0, ge=0)
    freshness_score: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TrustEvent(BaseModel):
    """Records a trust level transition for an agent type."""

    agent_type: str
    from_level: int = Field(ge=0, le=3)
    to_level: int = Field(ge=0, le=3)
    reason: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ViolationRecord(BaseModel):
    """A logged violation against an agent, feeding graduated sanctions."""

    agent_id: UUID
    agent_type: str
    violation_type: str
    severity: Severity
    description: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AgentTypeBaseline(BaseModel):
    """Performance baseline per agent type for regression drift detection."""

    agent_type: str
    token_efficiency: float = Field(ge=0.0)
    completion_rate: float = Field(ge=0.0, le=1.0)
    exit_report_quality: float = Field(ge=0.0, le=1.0)
    mandate_count: int = Field(default=0, ge=0)
    first_recorded_at: datetime = Field(default_factory=datetime.utcnow)
    last_recalculated_at: datetime = Field(default_factory=datetime.utcnow)


class SpawnGateResult(BaseModel):
    """Result of the three pre-spawn gates: overlap, pressure, memory retrieval."""

    overlap_detected: bool = False
    overlap_details: list[dict[str, Any]] = Field(default_factory=list)
    resource_pressure: float = Field(default=0.0, ge=0.0, le=1.0)
    pressure_level: PressureLevel = "green"
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    integrity_valid: bool = True
    approved: bool = True
    justification_required: bool = False


class VitalSigns(BaseModel):
    """System-level health metrics -- five vital signs plus pressure."""

    turnover_ratio: float = Field(ge=0.0)
    token_efficiency_trend: float = Field(ge=0.0)
    handoff_utilization: float = Field(ge=0.0, le=1.0)
    team_memory_freshness: float = Field(ge=0.0)
    output_consumption: float = Field(ge=0.0, le=1.0)
    global_pressure: float = Field(ge=0.0, le=1.0)
    pressure_level: PressureLevel = "green"
    computed_at: datetime = Field(default_factory=datetime.utcnow)


class InnateSignal(BaseModel):
    """Behavioral anomaly detection signal from the innate detection layer."""

    agent_id: UUID
    signal_type: SignalType
    description: str
    severity: SignalSeverity = "warning"
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class OnboardingContext(BaseModel):
    """Genesis-phase context bundle delivered to a newly spawned agent."""

    mandate: str
    orientation: dict[str, Any] = Field(default_factory=dict)
    compliance_rules: list[str] = Field(default_factory=list)
    team_memory: list[dict[str, Any]] = Field(default_factory=list)
    predecessor_reports: list[dict[str, Any]] = Field(default_factory=list)
    memos: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request / Response models (API layer)
# ---------------------------------------------------------------------------


class AgentCreate(BaseModel):
    """POST /agents -- register a new agent."""

    type: str
    name: str
    mandate: str
    framework: Framework = "custom"
    parent_id: UUID | None = None
    parent_ids: list[UUID] = Field(default_factory=list)
    generation: int = Field(default=0, ge=0)
    team_id: UUID | None = None
    territory: list[str] = Field(default_factory=list)
    mandate_type: MandateType | None = None
    definition_hash: str | None = None
    tokens_expected: Literal["low", "medium", "high"] = "medium"
    metadata: dict[str, Any] | None = None


class AgentUpdate(BaseModel):
    """PATCH /agents/{id} -- partial update of an agent record."""

    name: str | None = None
    status: AgentStatus | None = None
    phase: AgentPhase | None = None
    mandate_echo: str | None = Field(default=None, max_length=200)
    trust_level: int | None = Field(default=None, ge=0, le=3)
    tokens_consumed: int | None = Field(default=None, ge=0)
    team_id: UUID | None = None
    archived_at: datetime | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> AgentUpdate:
        set_fields = {
            k for k, v in self.__dict__.items() if v is not None
        }
        if not set_fields:
            raise ValueError("At least one field must be provided for update")
        return self


class HeartbeatCreate(BaseModel):
    """POST /agents/{id}/heartbeat -- emit a semantic heartbeat."""

    last_action: str = Field(..., max_length=120)
    phase: AgentPhase
    action_count: int = Field(ge=0)
    momentum: Momentum = "neutral"
    tool_breakdown: dict[str, int] = Field(default_factory=dict)


class ExitReportCreate(BaseModel):
    """POST /agents/{id}/exit-report -- file an exit report at shutdown."""

    mandate_completed: bool
    key_findings: list[str] = Field(default_factory=list)
    what_worked: str = ""
    what_failed: str = ""
    recommendations: str = ""
    gaps: list[Gap] = Field(default_factory=list)
    decisions: list[DecisionNode] = Field(default_factory=list)
    dissent: list[DissentRecord] = Field(default_factory=list)
    contrarian: str | None = None
    tokens_consumed: int = Field(default=0, ge=0)


class OnboardingRequest(BaseModel):
    """POST /agents/{id}/onboard -- request onboarding context for an agent."""

    mandate: str
    team_id: UUID | None = None
    framework: Framework = "custom"
    include_team_memory: bool = True
    include_predecessor_reports: bool = True
    include_memos: bool = True


class OnboardingResponse(BaseModel):
    """Response to onboarding request -- the full genesis context bundle."""

    agent_id: UUID
    context: OnboardingContext
    spawn_gate: SpawnGateResult
    trust_level: int = Field(ge=0, le=3)
    tokens_expected: Literal["low", "medium", "high"] = "medium"
