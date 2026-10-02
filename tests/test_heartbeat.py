"""Tests for HeartbeatService — heartbeats, staleness, anomaly detection."""

import pytest
from datetime import datetime, timedelta, timezone

from agenthr.database import Agent, Heartbeat
from agenthr.services.heartbeat import HeartbeatService


# -- record heartbeat -------------------------------------------------------


@pytest.mark.asyncio
async def test_record_heartbeat(session, sample_agent):
    svc = HeartbeatService(session)
    result = await svc.record(str(sample_agent.id), {
        "last_action": "Read src/data/enrollment.csv",
        "phase": "research",
        "action_count": 3,
        "momentum": "converging",
    })
    await session.commit()

    assert result["heartbeat"]["agent_id"] == str(sample_agent.id)
    assert result["heartbeat"]["last_action"] == "Read src/data/enrollment.csv"
    assert result["signals"] == []


@pytest.mark.asyncio
async def test_record_heartbeat_unknown_agent(session):
    svc = HeartbeatService(session)
    with pytest.raises(ValueError, match="not found"):
        await svc.record("00000000-0000-0000-0000-000000000001", {
            "last_action": "test",
            "phase": "research",
            "action_count": 0,
        })


# -- get latest / history ---------------------------------------------------


@pytest.mark.asyncio
async def test_get_latest_no_heartbeats(session, sample_agent):
    svc = HeartbeatService(session)
    result = await svc.get_latest(str(sample_agent.id))
    assert result is None


@pytest.mark.asyncio
async def test_get_history(session, sample_agent):
    svc = HeartbeatService(session)
    for i in range(3):
        await svc.record(str(sample_agent.id), {
            "last_action": f"action-{i}",
            "phase": "research",
            "action_count": i,
        })
    await session.flush()

    history = await svc.get_history(str(sample_agent.id), limit=10)
    assert len(history) == 3
    # Most recent first
    assert history[0]["last_action"] == "action-2"


# -- staleness detection ----------------------------------------------------


@pytest.mark.asyncio
async def test_detect_stale_no_heartbeats(session, sample_agent):
    """An active agent with no heartbeats should be detected as stale."""
    svc = HeartbeatService(session)
    stale = await svc.detect_stale(threshold_minutes=1)
    assert len(stale) == 1
    assert stale[0]["agent_id"] == str(sample_agent.id)


@pytest.mark.asyncio
async def test_detect_stale_fresh_agent(session, sample_agent):
    """An agent with a recent heartbeat is not stale."""
    svc = HeartbeatService(session)
    await svc.record(str(sample_agent.id), {
        "last_action": "working",
        "phase": "research",
        "action_count": 1,
    })
    await session.flush()

    stale = await svc.detect_stale(threshold_minutes=15)
    assert len(stale) == 0


# -- anomaly detection: stuck loop ------------------------------------------


@pytest.mark.asyncio
async def test_stuck_loop_detected(session, sample_agent):
    svc = HeartbeatService(session)
    agent_id = str(sample_agent.id)

    # Record 3 identical actions
    for _ in range(2):
        await svc.record(agent_id, {
            "last_action": "Grep for TODO",
            "phase": "research",
            "action_count": 5,
        })
    await session.flush()

    # Third one should trigger stuck_loop
    result = await svc.record(agent_id, {
        "last_action": "Grep for TODO",
        "phase": "research",
        "action_count": 6,
    })
    await session.flush()

    signal_types = [s["signal_type"] for s in result["signals"]]
    assert "stuck_loop" in signal_types


@pytest.mark.asyncio
async def test_no_stuck_loop_different_actions(session, sample_agent):
    svc = HeartbeatService(session)
    agent_id = str(sample_agent.id)

    actions = ["Read file A", "Edit file B", "Grep for pattern"]
    for i, action in enumerate(actions):
        result = await svc.record(agent_id, {
            "last_action": action,
            "phase": "execution",
            "action_count": i,
        })
    await session.flush()

    signal_types = [s["signal_type"] for s in result["signals"]]
    assert "stuck_loop" not in signal_types


# -- anomaly detection: territory drift -------------------------------------


@pytest.mark.asyncio
async def test_territory_drift_detected(session, sample_agent):
    """Agent with territory writing outside it should trigger drift."""
    svc = HeartbeatService(session)
    result = await svc.record(str(sample_agent.id), {
        "last_action": "wrote to /etc/config/secrets.yaml",
        "phase": "execution",
        "action_count": 5,
    })

    signal_types = [s["signal_type"] for s in result["signals"]]
    assert "territory_drift" in signal_types


@pytest.mark.asyncio
async def test_no_territory_drift_within_territory(session, sample_agent):
    """Agent working within its territory should not trigger drift."""
    svc = HeartbeatService(session)
    result = await svc.record(str(sample_agent.id), {
        "last_action": "wrote to src/data/output.csv",
        "phase": "execution",
        "action_count": 5,
    })

    signal_types = [s["signal_type"] for s in result["signals"]]
    assert "territory_drift" not in signal_types


# -- anomaly detection: output contradiction --------------------------------


@pytest.mark.asyncio
async def test_output_contradiction(session, sample_agent):
    svc = HeartbeatService(session)
    result = await svc.record(str(sample_agent.id), {
        "last_action": "Rewriting report for the 5th time",
        "phase": "output",
        "action_count": 25,
        "momentum": "diverging",
    })

    signal_types = [s["signal_type"] for s in result["signals"]]
    assert "output_contradiction" in signal_types


@pytest.mark.asyncio
async def test_no_contradiction_low_action_count(session, sample_agent):
    svc = HeartbeatService(session)
    result = await svc.record(str(sample_agent.id), {
        "last_action": "Starting work",
        "phase": "research",
        "action_count": 5,
        "momentum": "diverging",
    })

    signal_types = [s["signal_type"] for s in result["signals"]]
    assert "output_contradiction" not in signal_types
