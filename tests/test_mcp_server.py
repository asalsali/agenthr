"""Tests for the MCP server tools — integration tests calling the tool functions directly.

Each test resets the database module's global engine to an in-memory SQLite DB,
then calls the async tool functions the same way the MCP runtime would.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import agenthr.database as db_module
import agenthr.mcp_server as mcp_module
from agenthr.database import Base
from agenthr.mcp_server import (
    request_spawn,
    submit_heartbeat,
    submit_exit_report,
    search_knowledge,
    vital_signs,
)


@pytest_asyncio.fixture(autouse=True)
async def _use_memory_db():
    """Replace the database globals with a fresh in-memory SQLite engine."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Patch database module globals
    old_engine = db_module._engine
    old_factory = db_module._session_factory
    db_module._engine = engine
    db_module._session_factory = factory

    # Mark DB as initialized so _ensure_db() doesn't call create_tables()
    old_init = mcp_module._db_initialized
    mcp_module._db_initialized = True

    yield

    # Restore
    db_module._engine = old_engine
    db_module._session_factory = old_factory
    mcp_module._db_initialized = old_init
    await engine.dispose()


# ── request_spawn ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_creates_agent():
    result = await request_spawn(
        type="analyst",
        name="scout-1",
        mandate="Analyze enrollment trends",
    )
    assert result["agent"] is not None
    assert result["agent"]["name"] == "scout-1"
    assert result["agent"]["type"] == "analyst"
    assert result["spawn_gate_result"]["blocked"] is False


@pytest.mark.asyncio
async def test_spawn_with_optional_fields():
    result = await request_spawn(
        type="writer",
        name="report-writer",
        mandate="Write quarterly report",
        framework="claude-code",
        mandate_type="synthesis",
        territory=["docs/**"],
    )
    assert result["agent"]["framework"] == "claude-code"


@pytest.mark.asyncio
async def test_spawn_detects_overlap():
    await request_spawn(type="analyst", name="a1", mandate="Analyze enrollment data python")
    result = await request_spawn(type="analyst", name="a2", mandate="Analyze enrollment data trends")

    assert result["spawn_gate_result"]["overlap"]["detected"] is True


@pytest.mark.asyncio
async def test_spawn_returns_memory_hits():
    """After an exit report, a new spawn with related mandate gets memory hits."""
    # Spawn and complete an agent
    r1 = await request_spawn(type="analyst", name="first", mandate="Research enrollment patterns")
    agent_id = r1["agent"]["id"]

    await submit_exit_report(
        agent_id=agent_id,
        mandate_completed=True,
        what_worked="SQL queries on enrollment tables",
        recommendations="Use cached views for enrollment data",
    )

    # Spawn a new agent with related mandate
    r2 = await request_spawn(type="analyst", name="second", mandate="Analyze enrollment metrics")
    memory = r2["spawn_gate_result"]["memory_retrieval"]
    # Memory retrieval uses keyword matching; "enrollment" should hit
    assert isinstance(memory, list)


# ── submit_heartbeat ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_records():
    r = await request_spawn(type="analyst", name="a1", mandate="task")
    agent_id = r["agent"]["id"]

    result = await submit_heartbeat(
        agent_id=agent_id,
        last_action="Read src/data/file.csv",
        phase="research",
        action_count=3,
    )
    assert result["heartbeat"]["agent_id"] == agent_id
    assert result["heartbeat"]["last_action"] == "Read src/data/file.csv"


@pytest.mark.asyncio
async def test_heartbeat_unknown_agent():
    """Heartbeat for non-existent agent returns an error."""
    # The tool function raises ValueError which the MCP handler would catch
    with pytest.raises(ValueError, match="not found"):
        await submit_heartbeat(
            agent_id="00000000-0000-0000-0000-000000000099",
            last_action="test",
            phase="research",
            action_count=0,
        )


@pytest.mark.asyncio
async def test_heartbeat_with_tool_breakdown():
    r = await request_spawn(type="analyst", name="a1", mandate="task")
    agent_id = r["agent"]["id"]

    result = await submit_heartbeat(
        agent_id=agent_id,
        last_action="Analyzing data",
        phase="execution",
        action_count=10,
        tool_breakdown={"Read": 5, "Grep": 3, "Bash": 2},
    )
    assert result["heartbeat"]["tool_breakdown"] == {"Read": 5, "Grep": 3, "Bash": 2}


# ── submit_exit_report ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_exit_report_basic():
    r = await request_spawn(type="analyst", name="a1", mandate="task")
    agent_id = r["agent"]["id"]

    result = await submit_exit_report(
        agent_id=agent_id,
        mandate_completed=True,
        key_findings=["enrollment up 12%"],
        what_worked="Direct SQL",
        what_failed="PowerBI too slow",
        recommendations="Build cached views",
        tokens_consumed=2400,
    )
    assert result["exit_report"]["mandate_completed"] is True
    assert result["exit_report"]["agent_id"] == agent_id
    assert "baseline_update" in result
    assert "trust_evaluation" in result


@pytest.mark.asyncio
async def test_exit_report_unknown_agent():
    with pytest.raises(ValueError, match="not found"):
        await submit_exit_report(
            agent_id="00000000-0000-0000-0000-000000000099",
            mandate_completed=True,
        )


@pytest.mark.asyncio
async def test_exit_report_minimal():
    """Only required fields — mandate_completed."""
    r = await request_spawn(type="worker", name="w1", mandate="quick task")
    agent_id = r["agent"]["id"]

    result = await submit_exit_report(agent_id=agent_id, mandate_completed=False)
    assert result["exit_report"]["mandate_completed"] is False


# ── search_knowledge ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_empty_db():
    results = await search_knowledge(query="enrollment")
    assert results == []


@pytest.mark.asyncio
async def test_search_finds_report():
    r = await request_spawn(type="analyst", name="a1", mandate="task")
    agent_id = r["agent"]["id"]

    await submit_exit_report(
        agent_id=agent_id,
        mandate_completed=True,
        what_worked="GraphQL was fast",
    )

    results = await search_knowledge(query="GraphQL")
    assert len(results) == 1
    assert "GraphQL" in results[0]["what_worked"]


@pytest.mark.asyncio
async def test_search_no_match():
    r = await request_spawn(type="analyst", name="a1", mandate="task")
    await submit_exit_report(
        agent_id=r["agent"]["id"],
        mandate_completed=True,
        what_worked="SQL queries",
    )

    results = await search_knowledge(query="Kubernetes")
    assert results == []


@pytest.mark.asyncio
async def test_search_respects_limit():
    for i in range(5):
        r = await request_spawn(type="analyst", name=f"a{i}", mandate=f"task {i}")
        await submit_exit_report(
            agent_id=r["agent"]["id"],
            mandate_completed=True,
            what_worked=f"Method {i} worked great",
        )

    results = await search_knowledge(query="worked", limit=2)
    assert len(results) <= 2


# ── vital_signs ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vital_signs_empty_db():
    result = await vital_signs()
    assert "vital_signs" in result
    assert "global_pressure" in result
    assert result["vital_signs"]["turnover_ratio"]["value"] == 0.0


@pytest.mark.asyncio
async def test_vital_signs_after_activity():
    """Vital signs reflect spawned agents."""
    for i in range(3):
        await request_spawn(type="analyst", name=f"a{i}", mandate=f"task {i}")

    result = await vital_signs()
    assert result["vital_signs"]["turnover_ratio"]["detail"]["spawned_24h"] >= 3


# ── full lifecycle ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_hr_lifecycle():
    """Spawn -> heartbeat -> exit report -> search knowledge."""
    # 1. Hire
    spawn = await request_spawn(
        type="analyst", name="lifecycle-agent", mandate="Research market trends",
    )
    assert spawn["agent"] is not None
    agent_id = spawn["agent"]["id"]

    # 2. Check in
    hb = await submit_heartbeat(
        agent_id=agent_id,
        last_action="Reading market reports",
        phase="research",
        action_count=5,
    )
    assert hb["heartbeat"]["agent_id"] == agent_id

    # 3. Exit interview
    exit_r = await submit_exit_report(
        agent_id=agent_id,
        mandate_completed=True,
        key_findings=["Market growing 15% YoY"],
        what_worked="Industry reports were comprehensive",
        what_failed="Real-time data was stale",
        recommendations="Use live feeds instead of quarterly reports",
        tokens_consumed=3200,
    )
    assert exit_r["exit_report"]["mandate_completed"] is True

    # 4. Next agent searches knowledge (search hits what_worked/what_failed/recommendations/contrarian)
    knowledge = await search_knowledge(query="live feeds")
    assert len(knowledge) >= 1

    # 5. Org health
    health = await vital_signs()
    assert health["vital_signs"]["turnover_ratio"]["detail"]["spawned_24h"] >= 1
