"""AgentHR REST API — FastAPI application."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query

from agenthr.database import create_tables, get_session as async_session
from agenthr.models import (
    AgentCreate,
    AgentUpdate,
    HeartbeatCreate,
    ExitReportCreate,
    OnboardingRequest,
)
from agenthr.services.registry import RegistryService
from agenthr.services.heartbeat import HeartbeatService
from agenthr.services.evaluate import EvaluateService
from agenthr.services.analytics import AnalyticsService


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    yield


app = FastAPI(
    title="AgentHR",
    description="HR for AI agents — workforce management for agent fleets",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Helpers ──────────────────────────────────────────────────────


async def _svc():
    """Yield all four services sharing one session."""
    async with async_session() as session:
        yield (
            RegistryService(session),
            HeartbeatService(session),
            EvaluateService(session),
            AnalyticsService(session),
            session,
        )


# ── Registry endpoints ──────────────────────────────────────────


@app.post("/agents", status_code=201)
async def hire_agent(body: AgentCreate):
    """Register (hire) a new agent. Runs spawn gates."""
    async with async_session() as session:
        svc = RegistryService(session)
        result = await svc.hire(body.model_dump())
        await session.commit()
        return result


@app.get("/agents")
async def list_agents(
    status: str | None = None,
    team_id: str | None = None,
    type: str | None = None,
    trust_level: int | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List agents with optional filters."""
    async with async_session() as session:
        svc = RegistryService(session)
        return await svc.list(
            status=status,
            team_id=team_id,
            type=type,
            trust_level=trust_level,
            limit=limit,
            offset=offset,
        )


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    """Get agent profile."""
    async with async_session() as session:
        svc = RegistryService(session)
        agent = await svc.get(agent_id)
        if not agent:
            raise HTTPException(404, "Agent not found")
        return agent


@app.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate):
    """Update agent fields."""
    async with async_session() as session:
        svc = RegistryService(session)
        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(400, "No fields to update")
        result = await svc.update(agent_id, updates)
        await session.commit()
        return result


@app.delete("/agents/{agent_id}")
async def terminate_agent(agent_id: str):
    """Archive (terminate) an agent."""
    async with async_session() as session:
        svc = RegistryService(session)
        result = await svc.terminate(agent_id)
        await session.commit()
        return result


@app.get("/agents/roster/stats")
async def roster_stats():
    """Roster summary: counts by status, type, team, trust level."""
    async with async_session() as session:
        svc = RegistryService(session)
        return await svc.get_roster_stats()


@app.get("/agents/pressure")
async def resource_pressure():
    """Current resource pressure (0.0-1.0) and level."""
    async with async_session() as session:
        svc = RegistryService(session)
        return await svc.compute_pressure()


# ── Heartbeat endpoints ─────────────────────────────────────────


@app.post("/agents/{agent_id}/heartbeat")
async def record_heartbeat(agent_id: str, body: HeartbeatCreate):
    """Record a heartbeat. Runs innate detection."""
    async with async_session() as session:
        svc = HeartbeatService(session)
        result = await svc.record(agent_id, body.model_dump())
        await session.commit()
        return result


@app.get("/agents/{agent_id}/heartbeat")
async def get_latest_heartbeat(agent_id: str):
    """Get most recent heartbeat."""
    async with async_session() as session:
        svc = HeartbeatService(session)
        hb = await svc.get_latest(agent_id)
        if not hb:
            raise HTTPException(404, "No heartbeats found")
        return hb


@app.get("/agents/{agent_id}/heartbeats")
async def heartbeat_history(agent_id: str, limit: int = 50):
    """Get heartbeat history."""
    async with async_session() as session:
        svc = HeartbeatService(session)
        return await svc.get_history(agent_id, limit)


@app.get("/heartbeats/stale")
async def stale_agents(threshold_minutes: int = 15):
    """Find agents with stale heartbeats."""
    async with async_session() as session:
        svc = HeartbeatService(session)
        return await svc.detect_stale(threshold_minutes)


# ── Evaluate endpoints ──────────────────────────────────────────


@app.post("/agents/{agent_id}/exit-report", status_code=201)
async def submit_exit_report(agent_id: str, body: ExitReportCreate):
    """Submit exit report. Updates baselines and trust."""
    async with async_session() as session:
        svc = EvaluateService(session)
        result = await svc.submit_exit_report(agent_id, body.model_dump())
        await session.commit()
        return result


@app.get("/agents/{agent_id}/exit-report")
async def get_exit_report(agent_id: str):
    """Get exit report for an agent."""
    async with async_session() as session:
        svc = EvaluateService(session)
        report = await svc.get_exit_report(agent_id)
        if not report:
            raise HTTPException(404, "No exit report found")
        return report


@app.get("/exit-reports/search")
async def search_exit_reports(q: str, limit: int = 10):
    """Search exit reports by keyword."""
    async with async_session() as session:
        svc = EvaluateService(session)
        return await svc.search_reports(q, limit)


@app.get("/baselines/{agent_type}")
async def get_baseline(agent_type: str):
    """Get performance baseline for an agent type."""
    async with async_session() as session:
        svc = EvaluateService(session)
        baseline = await svc.get_baseline(agent_type)
        if not baseline:
            raise HTTPException(404, "No baseline found")
        return baseline


@app.get("/baselines/{agent_type}/degradation")
async def check_degradation(agent_type: str):
    """Check if an agent type has degraded from baseline."""
    async with async_session() as session:
        svc = EvaluateService(session)
        return await svc.check_degradation(agent_type)


@app.get("/trust/{agent_type}")
async def evaluate_trust(agent_type: str):
    """Evaluate trust level for an agent type."""
    async with async_session() as session:
        svc = EvaluateService(session)
        return await svc.evaluate_trust(agent_type)


@app.post("/trust/{agent_type}/promote")
async def promote_agent_type(agent_type: str, to_level: int, reason: str):
    """Promote an agent type's trust level."""
    async with async_session() as session:
        svc = EvaluateService(session)
        result = await svc.promote(agent_type, to_level, reason)
        await session.commit()
        return result


@app.post("/violations")
async def record_violation(
    agent_id: str,
    agent_type: str,
    violation_type: str,
    severity: str,
    description: str,
):
    """Record a violation with graduated sanctions."""
    async with async_session() as session:
        svc = EvaluateService(session)
        result = await svc.record_violation(agent_id, {
            "agent_type": agent_type,
            "violation_type": violation_type,
            "severity": severity,
            "description": description,
        })
        await session.commit()
        return result


# ── Analytics endpoints ─────────────────────────────────────────


@app.get("/analytics/vital-signs")
async def vital_signs():
    """System vital signs dashboard."""
    async with async_session() as session:
        svc = AnalyticsService(session)
        return await svc.vital_signs()


@app.get("/analytics/hotspots")
async def hotspots():
    """Find high-connectivity agent types."""
    async with async_session() as session:
        svc = AnalyticsService(session)
        return await svc.hotspots()


@app.get("/analytics/futility")
async def futility_report():
    """Find agents whose output was never consumed."""
    async with async_session() as session:
        svc = AnalyticsService(session)
        return await svc.futility_report()


@app.get("/analytics/teams/{team_id}")
async def team_health(team_id: str):
    """Per-team health analytics."""
    async with async_session() as session:
        svc = AnalyticsService(session)
        return await svc.team_health(team_id)


@app.get("/analytics/types/{agent_type}")
async def agent_type_report(agent_type: str):
    """Full report on an agent type."""
    async with async_session() as session:
        svc = AnalyticsService(session)
        return await svc.agent_type_report(agent_type)


# ── Onboarding endpoint ─────────────────────────────────────────


@app.post("/agents/{agent_id}/onboard")
async def onboard_agent(agent_id: str, body: OnboardingRequest):
    """Run onboarding (Genesis Phase). Returns context + alignment check."""
    async with async_session() as session:
        registry = RegistryService(session)
        evaluate = EvaluateService(session)

        agent = await registry.get(agent_id)
        if not agent:
            raise HTTPException(404, "Agent not found")

        # Gather onboarding context
        team_reports = []
        if agent.get("team_id"):
            team_reports = await evaluate.search_reports(
                agent.get("mandate", ""), limit=5
            )

        predecessor_reports = await evaluate.search_reports(
            agent.get("mandate", ""), limit=3
        )

        context = {
            "mandate": agent.get("mandate", ""),
            "orientation": body.orientation or {},
            "compliance_rules": body.compliance_rules or [],
            "team_memory": team_reports,
            "predecessor_reports": predecessor_reports,
            "memos": body.memos or [],
        }

        # Update agent status to active
        await registry.update(agent_id, {"status": "active", "phase": "research"})
        await session.commit()

        return {
            "agent_id": agent_id,
            "onboarding_context": context,
            "status": "active",
            "message": "Agent onboarded. Context delivered. Ready to execute.",
        }
