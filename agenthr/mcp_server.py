"""AgentHR MCP Server — the HR department as an MCP tool.

Exposes AgentHR's core lifecycle tools via the Model Context Protocol so any
MCP-compatible agent system (Claude Code, Cursor, Windsurf, etc.) can interact
with HR the way it interacts with any tool.

Phase 1 tools (minimum viable HR):
  - request_spawn      — hiring (spawn gates)
  - submit_heartbeat   — performance check-in
  - submit_exit_report — exit interview
  - search_knowledge   — institutional memory
  - vital_signs        — organizational health
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import FastMCP

from agenthr.database import create_tables, get_session as async_session
from agenthr.models import AgentCreate, HeartbeatCreate, ExitReportCreate
from agenthr.services.registry import RegistryService
from agenthr.services.heartbeat import HeartbeatService
from agenthr.services.evaluate import EvaluateService
from agenthr.services.analytics import AnalyticsService


mcp = FastMCP(
    "agenthr",
    instructions=(
        "AgentHR is the HR department for AI agents. Use these tools to manage "
        "the agent workforce: request_spawn before starting new agents, "
        "submit_heartbeat during work, submit_exit_report when done, "
        "search_knowledge to learn from past agents, and vital_signs to check "
        "organizational health."
    ),
)


# ── Startup ─────────────────────────────────────────────────────

_db_initialized = False


async def _ensure_db():
    global _db_initialized
    if not _db_initialized:
        await create_tables()
        _db_initialized = True


# ── Tools ───────────────────────────────────────────────────────


@mcp.tool()
async def request_spawn(
    type: str,
    name: str,
    mandate: str,
    framework: str = "custom",
    team_id: str | None = None,
    mandate_type: str | None = None,
    territory: list[str] | None = None,
) -> dict[str, Any]:
    """Submit a job application to HR.

    Runs the 4 spawn gates (overlap detection, resource pressure, memory
    retrieval, integrity check), creates the agent record, and returns
    the result with gate details. Use this before starting any new agent.
    """
    await _ensure_db()
    data = {
        "type": type,
        "name": name,
        "mandate": mandate,
        "framework": framework,
    }
    if team_id is not None:
        data["team_id"] = team_id
    if mandate_type is not None:
        data["mandate_type"] = mandate_type
    if territory is not None:
        data["territory"] = territory

    agent_create = AgentCreate(**data)
    async with async_session() as session:
        svc = RegistryService(session)
        result = await svc.hire(agent_create.model_dump())
        await session.commit()
        return result


@mcp.tool()
async def submit_heartbeat(
    agent_id: str,
    last_action: str,
    phase: str,
    action_count: int,
    momentum: str = "neutral",
    tool_breakdown: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Check in with HR.

    Report what you're doing, what phase you're in, and what tools you're
    using. HR runs anomaly detection and flags issues (stuck loops, token
    hemorrhage, territory drift, role drift).
    """
    await _ensure_db()
    heartbeat_data = HeartbeatCreate(
        last_action=last_action,
        phase=phase,
        action_count=action_count,
        momentum=momentum,
        tool_breakdown=tool_breakdown or {},
    )
    async with async_session() as session:
        svc = HeartbeatService(session)
        result = await svc.record(agent_id, heartbeat_data.model_dump())
        await session.commit()
        return result


@mcp.tool()
async def submit_exit_report(
    agent_id: str,
    mandate_completed: bool,
    key_findings: list[str] | None = None,
    what_worked: str = "",
    what_failed: str = "",
    recommendations: str = "",
    contrarian: str | None = None,
    tokens_consumed: int = 0,
) -> dict[str, Any]:
    """File an exit interview with HR.

    Report what worked, what failed, key findings, recommendations, and
    gaps. HR updates baselines and trust scores. Every agent should submit
    this before shutting down.
    """
    await _ensure_db()
    report_data = ExitReportCreate(
        mandate_completed=mandate_completed,
        key_findings=key_findings or [],
        what_worked=what_worked,
        what_failed=what_failed,
        recommendations=recommendations,
        contrarian=contrarian,
        tokens_consumed=tokens_consumed,
    )
    async with async_session() as session:
        svc = EvaluateService(session)
        result = await svc.submit_exit_report(agent_id, report_data.model_dump())
        await session.commit()
        return result


@mcp.tool()
async def search_knowledge(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search HR's institutional knowledge base.

    Find what previous agents learned about a topic. Returns relevant exit
    reports from all past agents, ranked by keyword relevance.
    """
    await _ensure_db()
    async with async_session() as session:
        svc = EvaluateService(session)
        return await svc.search_reports(query, limit)


@mcp.tool()
async def vital_signs() -> dict[str, Any]:
    """Check organizational health.

    Returns 5 vital signs: turnover ratio, token efficiency, handoff
    utilization, team memory freshness, and output consumption — plus
    global resource pressure.
    """
    await _ensure_db()
    async with async_session() as session:
        svc = AnalyticsService(session)
        return await svc.vital_signs()


# ── Entry point ─────────────────────────────────────────────────


def main():
    """Run the MCP server over stdio."""
    mcp.run("stdio")
