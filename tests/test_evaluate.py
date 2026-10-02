"""Tests for EvaluateService — exit reports, baselines, trust, violations."""

import pytest
from agenthr.database import Agent, ExitReport, Violation
from agenthr.services.evaluate import EvaluateService, _report_quality


# -- report quality scoring -------------------------------------------------


def test_report_quality_full_report():
    """A report with all fields filled scores high."""
    report = ExitReport(
        agent_id="00000000-0000-0000-0000-000000000001",
        mandate_completed=True,
        key_findings=["finding 1"],
        what_worked="SQL queries",
        what_failed="API too slow",
        recommendations="Use cached views",
        gaps=[],
        decisions=[],
        dissent=[],
        contrarian="Growth may plateau",
    )
    quality = _report_quality(report)
    assert quality > 0.5


def test_report_quality_empty_report():
    """A report with only mandate_completed=False scores low."""
    report = ExitReport(
        agent_id="00000000-0000-0000-0000-000000000001",
        mandate_completed=False,
    )
    quality = _report_quality(report)
    assert quality < 0.2


# -- submit exit report -----------------------------------------------------


@pytest.mark.asyncio
async def test_submit_exit_report(session, sample_agent):
    svc = EvaluateService(session)
    result = await svc.submit_exit_report(str(sample_agent.id), {
        "mandate_completed": True,
        "key_findings": ["enrollment up 12%"],
        "what_worked": "Direct SQL queries",
        "what_failed": "PowerBI API too slow",
        "recommendations": "Build cached view",
        "tokens_consumed": 2400,
    })
    await session.commit()

    assert result["exit_report"]["mandate_completed"] is True
    assert result["exit_report"]["agent_id"] == str(sample_agent.id)
    assert "baseline_update" in result
    assert "trust_evaluation" in result


@pytest.mark.asyncio
async def test_submit_exit_report_unknown_agent(session):
    svc = EvaluateService(session)
    with pytest.raises(ValueError, match="not found"):
        await svc.submit_exit_report(
            "00000000-0000-0000-0000-000000000099",
            {"mandate_completed": True},
        )


# -- search reports ---------------------------------------------------------


@pytest.mark.asyncio
async def test_search_reports_finds_match(session, sample_agent):
    svc = EvaluateService(session)
    await svc.submit_exit_report(str(sample_agent.id), {
        "mandate_completed": True,
        "what_worked": "GraphQL queries were fast",
        "what_failed": "REST endpoints were slow",
    })
    await session.commit()

    results = await svc.search_reports("GraphQL")
    assert len(results) == 1
    assert "GraphQL" in results[0]["what_worked"]


@pytest.mark.asyncio
async def test_search_reports_no_match(session, sample_agent):
    svc = EvaluateService(session)
    await svc.submit_exit_report(str(sample_agent.id), {
        "mandate_completed": True,
        "what_worked": "SQL queries",
    })
    await session.commit()

    results = await svc.search_reports("Kubernetes")
    assert len(results) == 0


# -- baselines and degradation ----------------------------------------------


@pytest.mark.asyncio
async def test_baseline_created_after_report(session, sample_agent):
    svc = EvaluateService(session)
    await svc.submit_exit_report(str(sample_agent.id), {
        "mandate_completed": True,
        "tokens_consumed": 1000,
    })
    await session.commit()

    baseline = await svc.get_baseline("analyst")
    assert baseline is not None
    assert baseline["mandate_count"] == 1


@pytest.mark.asyncio
async def test_degradation_no_baseline(session):
    svc = EvaluateService(session)
    result = await svc.check_degradation("nonexistent-type")
    assert result["degraded"] is False
    assert "No baseline" in result["reason"]


# -- trust evaluation -------------------------------------------------------


@pytest.mark.asyncio
async def test_trust_starts_at_zero(session, sample_agent):
    svc = EvaluateService(session)
    trust = await svc.evaluate_trust("analyst")
    assert trust["current_level"] == 0


@pytest.mark.asyncio
async def test_trust_promotion_requires_mandates(session, sample_agent):
    svc = EvaluateService(session)
    trust = await svc.evaluate_trust("analyst")
    assert trust["eligible_for_promotion"] is False
    assert any("3+" in b for b in trust["blockers"])


@pytest.mark.asyncio
async def test_trust_eligible_after_mandates(session):
    """After 3 completed mandates with no violations, level 1 is eligible."""
    svc = EvaluateService(session)

    for i in range(3):
        agent = Agent(
            type="researcher", name=f"r-{i}", framework="custom",
            mandate=f"research task {i}", status="archived", phase="shutdown",
        )
        session.add(agent)
        await session.flush()

        await svc.submit_exit_report(str(agent.id), {
            "mandate_completed": True,
            "key_findings": [f"finding {i}"],
            "what_worked": "worked",
            "what_failed": "failed",
            "recommendations": "rec",
            "tokens_consumed": 500,
        })
    await session.commit()

    trust = await svc.evaluate_trust("researcher")
    assert trust["eligible_for_promotion"] is True
    assert trust["blockers"] == []


# -- violations and graduated sanctions -------------------------------------


@pytest.mark.asyncio
async def test_first_violation_noted(session, sample_agent):
    svc = EvaluateService(session)
    result = await svc.record_violation(str(sample_agent.id), {
        "agent_type": "analyst",
        "violation_type": "territory_drift",
        "severity": "incidental",
        "description": "Wrote outside territory",
    })
    await session.commit()

    sanction = result["sanction"]
    assert sanction["action"] == "noted"
    assert sanction["trust_impact"] == "none"
    assert result["total_violations_for_type"] == 1


@pytest.mark.asyncio
async def test_second_violation_freezes_promotion(session, sample_agent):
    svc = EvaluateService(session)

    for _ in range(2):
        result = await svc.record_violation(str(sample_agent.id), {
            "agent_type": "analyst",
            "violation_type": "stuck_loop",
            "severity": "systematic",
            "description": "Repeated violation",
        })
    await session.commit()

    sanction = result["sanction"]
    assert sanction["action"] == "warning"
    assert sanction["trust_impact"] == "freeze"
    assert result["total_violations_for_type"] == 2


@pytest.mark.asyncio
async def test_third_violation_demotes(session, sample_agent):
    svc = EvaluateService(session)

    sample_agent.trust_level = 1
    await session.flush()

    for _ in range(3):
        result = await svc.record_violation(str(sample_agent.id), {
            "agent_type": "analyst",
            "violation_type": "token_hemorrhage",
            "severity": "systematic",
            "description": "Repeated violation",
        })
    await session.commit()

    sanction = result["sanction"]
    assert sanction["action"] == "demotion"
    assert sanction["trust_impact"] == "demoted"
    assert sanction["demotion_result"]["from_level"] == 1
    assert sanction["demotion_result"]["to_level"] == 0
