"""Tests for AnalyticsService — vital signs, hotspots, futility."""

import pytest
from agenthr.database import Agent, ExitReport
from agenthr.services.analytics import AnalyticsService


# -- vital signs ------------------------------------------------------------


@pytest.mark.asyncio
async def test_vital_signs_empty_db(session):
    """Vital signs should return safe defaults on an empty database."""
    svc = AnalyticsService(session)
    result = await svc.vital_signs()

    assert "vital_signs" in result
    assert "global_pressure" in result
    # Empty DB: pressure is low but not necessarily zero (turnover component = 0.2)
    assert result["global_pressure"] <= 0.4

    vs = result["vital_signs"]
    assert vs["turnover_ratio"]["value"] == 0.0
    assert vs["handoff_utilization"]["value"] == 1.0  # no reports = no waste
    assert vs["team_memory_freshness"]["value"] == 0.0


@pytest.mark.asyncio
async def test_vital_signs_with_agents(session, sample_agent):
    svc = AnalyticsService(session)
    result = await svc.vital_signs()

    assert result["vital_signs"]["turnover_ratio"]["detail"]["spawned_24h"] >= 1


# -- hotspots ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_hotspots_empty_db(session):
    svc = AnalyticsService(session)
    hotspots = await svc.hotspots()
    assert hotspots == []


@pytest.mark.asyncio
async def test_hotspots_no_hotspot_with_few_children(session):
    """An agent type with few children is not a hotspot."""
    parent = Agent(
        type="manager", name="mgr-1", framework="custom",
        mandate="manage team", status="active", phase="execution",
    )
    session.add(parent)
    await session.flush()

    # Only 2 children — below fan_out threshold of 4
    for i in range(2):
        session.add(Agent(
            type="worker", name=f"w-{i}", framework="custom",
            mandate=f"task {i}", status="active", phase="execution",
            parent_id=parent.id,
        ))
    await session.flush()

    svc = AnalyticsService(session)
    hotspots = await svc.hotspots()
    # Manager has fan_out=2, below threshold
    manager_hotspots = [h for h in hotspots if h["agent_type"] == "manager"]
    assert len(manager_hotspots) == 0


# -- futility ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_futility_empty_db(session):
    svc = AnalyticsService(session)
    result = await svc.futility_report()
    assert result == []


@pytest.mark.asyncio
async def test_futility_detects_unconsumed_report(session):
    """A completed agent whose exit report was never read is futile."""
    agent = Agent(
        type="analyst", name="futile-1", framework="custom",
        mandate="research topic", status="archived", phase="shutdown",
    )
    session.add(agent)
    await session.flush()

    from datetime import datetime, timedelta, timezone
    old_time = datetime.now(timezone.utc) - timedelta(hours=48)
    report = ExitReport(
        agent_id=agent.id,
        mandate_completed=True,
        freshness_score=1.0,
        created_at=old_time,
    )
    session.add(report)
    await session.flush()

    svc = AnalyticsService(session)
    result = await svc.futility_report()
    assert len(result) >= 1
    assert result[0]["agent_name"] == "futile-1"
