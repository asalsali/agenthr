"""Tests for RegistryService — spawn gates, overlap, pressure."""

import pytest
from agenthr.database import Agent
from agenthr.services.registry import RegistryService, _keyword_tokens


# -- keyword tokenization ---------------------------------------------------


def test_keyword_tokens_removes_stop_words():
    tokens = _keyword_tokens("Analyze the data for this project")
    assert "the" not in tokens
    assert "for" not in tokens
    assert "analyze" in tokens
    assert "data" in tokens
    assert "project" in tokens


def test_keyword_tokens_lowercases():
    tokens = _keyword_tokens("Analyze DATA Project")
    assert tokens == {"analyze", "data", "project"}


def test_keyword_tokens_empty_string():
    assert _keyword_tokens("") == set()


# -- hire (spawn gates) -----------------------------------------------------


@pytest.mark.asyncio
async def test_hire_creates_agent(session):
    svc = RegistryService(session)
    result = await svc.hire({
        "type": "analyst",
        "name": "scout-1",
        "framework": "custom",
        "mandate": "Analyze enrollment trends",
    })
    await session.commit()

    assert result["agent"] is not None
    assert result["agent"]["name"] == "scout-1"
    assert result["agent"]["status"] == "onboarding"
    assert result["spawn_gate_result"]["blocked"] is False


@pytest.mark.asyncio
async def test_hire_detects_overlap(session):
    svc = RegistryService(session)

    # Hire first agent
    await svc.hire({
        "type": "analyst",
        "name": "scout-1",
        "framework": "custom",
        "mandate": "Analyze enrollment data python",
    })
    await session.commit()

    # Hire second with overlapping mandate
    result = await svc.hire({
        "type": "analyst",
        "name": "scout-2",
        "framework": "custom",
        "mandate": "Analyze enrollment data trends",
    })
    await session.commit()

    gate = result["spawn_gate_result"]
    assert gate["overlap"]["detected"] is True
    assert len(gate["overlap"]["agents"]) > 0


@pytest.mark.asyncio
async def test_hire_blocked_at_red_pressure(session):
    """Spawn gate blocks when pressure >= 0.9."""
    from agenthr.database import ExitReport
    svc = RegistryService(session, capacity=1)

    # density: 10 active / 1 capacity = capped 1.0 * 0.4 = 0.4
    # token_rate: 50k tokens / 10k budget = capped 1.0 * 0.2 = 0.2
    # turnover: 0 archived / 10 spawned = 0, so (1-0)*0.2 = 0.2
    # handoff: need unread reports to make (1-handoff)*0.2 > 0
    # Add unread exit reports: freshness_score=1.0 means unread
    for i in range(10):
        agent = Agent(
            type="worker", name=f"w-{i}", framework="custom",
            mandate=f"task {i}", status="active", phase="execution",
            tokens_consumed=50_000,
        )
        session.add(agent)
    await session.flush()

    # Add unread exit reports so handoff_util drops to 0 => (1-0)*0.2 = 0.2
    # Total: 0.4 + 0.2 + 0.2 + 0.2 = 1.0 (red)
    from sqlalchemy import select as sa_select
    agents = (await session.execute(sa_select(Agent))).scalars().all()
    for agent in agents:
        session.add(ExitReport(
            agent_id=agent.id,
            mandate_completed=True,
            freshness_score=1.0,  # never read
        ))
    await session.flush()

    result = await svc.hire({
        "type": "analyst",
        "name": "blocked-agent",
        "framework": "custom",
        "mandate": "This should be blocked",
    })

    pressure = result["spawn_gate_result"]["pressure"]["value"]
    assert pressure >= 0.9, f"Expected pressure >= 0.9, got {pressure}"
    assert result["spawn_gate_result"]["blocked"] is True
    assert result["agent"] is None


@pytest.mark.asyncio
async def test_hire_force_overrides_pressure_block(session):
    from agenthr.database import ExitReport
    svc = RegistryService(session, capacity=1)

    for i in range(10):
        agent = Agent(
            type="worker", name=f"w-{i}", framework="custom",
            mandate=f"task {i}", status="active", phase="execution",
            tokens_consumed=50_000,
        )
        session.add(agent)
    await session.flush()

    from sqlalchemy import select as sa_select
    agents = (await session.execute(sa_select(Agent))).scalars().all()
    for agent in agents:
        session.add(ExitReport(
            agent_id=agent.id, mandate_completed=True, freshness_score=1.0,
        ))
    await session.flush()

    result = await svc.hire({
        "type": "analyst",
        "name": "forced-agent",
        "framework": "custom",
        "mandate": "Force through pressure",
        "force": True,
    })

    assert result["spawn_gate_result"]["blocked"] is False
    assert result["agent"] is not None


# -- overlap detection -------------------------------------------------------


@pytest.mark.asyncio
async def test_check_overlap_no_agents(session):
    svc = RegistryService(session)
    overlaps = await svc.check_overlap("Analyze enrollment data", None)
    assert overlaps == []


@pytest.mark.asyncio
async def test_check_overlap_different_mandates(session):
    svc = RegistryService(session)
    session.add(Agent(
        type="writer", name="w-1", framework="custom",
        mandate="Write marketing copy for landing page",
        status="active", phase="execution",
    ))
    await session.flush()

    overlaps = await svc.check_overlap("Analyze database performance metrics", None)
    assert len(overlaps) == 0


# -- resource pressure -------------------------------------------------------


@pytest.mark.asyncio
async def test_pressure_zero_on_empty_db(session):
    svc = RegistryService(session)
    pressure = await svc.compute_pressure()
    # Empty DB: density=0, token_rate=0, turnover=0 so (1-0)*0.2=0.2, handoff=1.0 so (1-1)*0.2=0
    # Total = 0.2 (green)
    assert pressure["value"] <= 0.4
    assert pressure["level"] == "green"


@pytest.mark.asyncio
async def test_pressure_increases_with_agents(session):
    svc = RegistryService(session, capacity=10)
    for i in range(8):
        session.add(Agent(
            type="worker", name=f"w-{i}", framework="custom",
            mandate=f"task {i}", status="active", phase="execution",
        ))
    await session.flush()

    pressure = await svc.compute_pressure()
    assert pressure["value"] > 0.0
    assert pressure["components"]["agent_density"] > 0.5


# -- roster stats ------------------------------------------------------------


@pytest.mark.asyncio
async def test_roster_stats_empty(session):
    svc = RegistryService(session)
    stats = await svc.get_roster_stats()
    assert stats["total"] == 0


@pytest.mark.asyncio
async def test_roster_stats_counts(session, sample_agent):
    svc = RegistryService(session)
    stats = await svc.get_roster_stats()
    assert stats["total"] == 1
    assert stats["by_status"]["active"] == 1


# -- terminate ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminate_archives_agent(session, sample_agent):
    svc = RegistryService(session)
    result = await svc.terminate(str(sample_agent.id))
    await session.commit()

    assert result["status"] == "archived"
    assert result["archived_at"] is not None
