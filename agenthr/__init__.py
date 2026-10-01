"""AgentHR -- workforce management for AI agents.

Provides Pydantic v2 data models for agent lifecycle, trust governance,
health monitoring, spawn gates, and operational telemetry.
"""

from agenthr.models import (
    Agent,
    AgentCreate,
    AgentTypeBaseline,
    AgentUpdate,
    DecisionNode,
    DissentRecord,
    ExitReport,
    ExitReportCreate,
    Gap,
    Heartbeat,
    HeartbeatCreate,
    InnateSignal,
    OnboardingContext,
    OnboardingRequest,
    OnboardingResponse,
    SpawnGateResult,
    TrustEvent,
    ViolationRecord,
    VitalSigns,
)

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "AgentCreate",
    "AgentTypeBaseline",
    "AgentUpdate",
    "DecisionNode",
    "DissentRecord",
    "ExitReport",
    "ExitReportCreate",
    "Gap",
    "Heartbeat",
    "HeartbeatCreate",
    "InnateSignal",
    "OnboardingContext",
    "OnboardingRequest",
    "OnboardingResponse",
    "SpawnGateResult",
    "TrustEvent",
    "ViolationRecord",
    "VitalSigns",
]
