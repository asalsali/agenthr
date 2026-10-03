"""
EvaluateService -- Exit reports, baselines, trust progression, and violations.

Handles the evaluation lifecycle: submitting exit reports, computing
baselines per agent type, checking for regression degradation,
evaluating trust level eligibility, and applying graduated sanctions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthr.database import (
    Agent,
    Baseline,
    ExitReport,
    TrustEvent,
    Violation,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEGRADATION_THRESHOLD = 0.30  # 30% deviation = degraded
_EXIT_REPORT_FIELDS = [
    "mandate_completed", "key_findings", "what_worked", "what_failed",
    "recommendations", "gaps", "decisions", "dissent", "contrarian",
]

# Trust level criteria thresholds
_TRUST_CRITERIA = {
    1: {"min_mandates": 3, "max_violations": 0, "min_report_quality": 1.0},
    2: {"min_mandates": 10, "max_efficiency_deviation": 0.20, "min_completion_rate": 0.85},
    3: {"min_mandates": 25, "requires_endorsement": True},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _report_quality(report: ExitReport) -> float:
    """Fraction of non-empty exit report fields (0.0 -- 1.0)."""
    filled = 0
    for field in _EXIT_REPORT_FIELDS:
        val = getattr(report, field, None)
        if val is not None and val != "" and val != [] and val is not False:
            filled += 1
    return round(filled / len(_EXIT_REPORT_FIELDS), 2)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EvaluateService:
    """Exit reports, baselines, trust progression, and violations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # -- exit reports -------------------------------------------------------

    async def submit_exit_report(
        self, agent_id: str, report_data: dict
    ) -> dict:
        """Store an exit report.  Updates baselines and checks trust.

        Returns the created report, baseline update summary, and trust
        evaluation for the agent's type.
        """
        uid = uuid.UUID(agent_id)
        agent = await self.session.get(Agent, uid)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        report = ExitReport(
            agent_id=uid,
            mandate_completed=report_data.get("mandate_completed", False),
            key_findings=report_data.get("key_findings"),
            what_worked=report_data.get("what_worked"),
            what_failed=report_data.get("what_failed"),
            recommendations=report_data.get("recommendations"),
            gaps=report_data.get("gaps"),
            decisions=report_data.get("decisions"),
            dissent=report_data.get("dissent"),
            contrarian=report_data.get("contrarian"),
            tokens_consumed=report_data.get("tokens_consumed", agent.tokens_consumed),
            freshness_score=1.0,
        )
        self.session.add(report)
        await self.session.flush()

        quality = _report_quality(report)

        # Update baseline for this agent type
        baseline_update = await self._update_baseline(
            agent.type,
            tokens_consumed=report.tokens_consumed,
            mandate_completed=report.mandate_completed,
            report_quality=quality,
        )

        # Check trust eligibility
        trust_eval = await self.evaluate_trust(agent.type)

        return {
            "exit_report": _report_to_dict(report, quality),
            "baseline_update": baseline_update,
            "trust_evaluation": trust_eval,
        }

    async def get_exit_report(self, agent_id: str) -> dict | None:
        """Get the exit report for an agent (most recent if multiple)."""
        uid = uuid.UUID(agent_id)
        stmt = (
            select(ExitReport)
            .where(ExitReport.agent_id == uid)
            .order_by(ExitReport.created_at.desc())
            .limit(1)
        )
        report = (await self.session.execute(stmt)).scalar_one_or_none()
        if report is None:
            return None
        return _report_to_dict(report, _report_quality(report))

    async def search_reports(self, query: str, limit: int = 10) -> list[dict]:
        """Search exit reports by keyword match on textual fields.

        Uses case-insensitive LIKE queries on key_findings (JSON text),
        what_worked, what_failed, and recommendations.
        """
        pattern = f"%{query}%"
        # SQLite JSON is stored as text so LIKE works on the column directly.
        # key_findings is a JSON column, so cast to String for LIKE.
        stmt = (
            select(ExitReport)
            .where(
                cast(ExitReport.key_findings, String).ilike(pattern)
                | ExitReport.what_worked.ilike(pattern)
                | ExitReport.what_failed.ilike(pattern)
                | ExitReport.recommendations.ilike(pattern)
                | ExitReport.contrarian.ilike(pattern)
            )
            .order_by(ExitReport.created_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_report_to_dict(r, _report_quality(r)) for r in rows]

    # -- baselines ----------------------------------------------------------

    async def get_baseline(self, agent_type: str) -> dict | None:
        """Get the current baseline for an agent type."""
        stmt = select(Baseline).where(Baseline.agent_type == agent_type)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _baseline_to_dict(row) if row else None

    async def check_degradation(self, agent_type: str) -> dict:
        """Check whether an agent type has degraded >30% from baseline.

        Compares the last 5 exit reports against the stored baseline.
        """
        baseline = await self._get_baseline_obj(agent_type)
        if baseline is None:
            return {"degraded": False, "reason": "No baseline recorded yet."}

        # Gather last 5 completed reports for this agent type
        stmt = (
            select(ExitReport)
            .join(Agent, Agent.id == ExitReport.agent_id)
            .where(Agent.type == agent_type)
            .order_by(ExitReport.created_at.desc())
            .limit(5)
        )
        recent = (await self.session.execute(stmt)).scalars().all()
        if not recent:
            return {"degraded": False, "reason": "No recent exit reports."}

        # Token efficiency
        avg_tokens = sum(r.tokens_consumed for r in recent) / len(recent)
        token_deviation = (
            abs(avg_tokens - baseline.token_efficiency) / max(baseline.token_efficiency, 1)
        )

        # Completion rate
        completed = sum(1 for r in recent if r.mandate_completed)
        recent_completion = completed / len(recent)
        completion_deviation = (
            abs(recent_completion - baseline.completion_rate)
            / max(baseline.completion_rate, 0.01)
        )

        # Report quality
        avg_quality = sum(_report_quality(r) for r in recent) / len(recent)
        quality_deviation = (
            abs(avg_quality - baseline.exit_report_quality)
            / max(baseline.exit_report_quality, 0.01)
        )

        degraded = (
            token_deviation > _DEGRADATION_THRESHOLD
            or recent_completion < 0.70
            or quality_deviation > _DEGRADATION_THRESHOLD
        )

        return {
            "degraded": degraded,
            "metrics": {
                "token_efficiency": {
                    "baseline": baseline.token_efficiency,
                    "recent_avg": round(avg_tokens, 1),
                    "deviation": round(token_deviation, 3),
                },
                "completion_rate": {
                    "baseline": baseline.completion_rate,
                    "recent": round(recent_completion, 3),
                    "deviation": round(completion_deviation, 3),
                },
                "exit_report_quality": {
                    "baseline": baseline.exit_report_quality,
                    "recent_avg": round(avg_quality, 3),
                    "deviation": round(quality_deviation, 3),
                },
            },
            "threshold": _DEGRADATION_THRESHOLD,
            "sample_size": len(recent),
        }

    # -- trust evaluation ---------------------------------------------------

    async def evaluate_trust(self, agent_type: str) -> dict:
        """Evaluate trust level for an agent type.

        Returns current level, criteria status, promotion eligibility,
        and blockers.
        """
        # Current trust level: max trust_level among agents of this type
        stmt = (
            select(func.max(Agent.trust_level))
            .where(Agent.type == agent_type)
        )
        current_level = (await self.session.execute(stmt)).scalar_one() or 0

        # Mandate count (completed = has exit report with mandate_completed=True)
        mandate_stmt = (
            select(func.count(ExitReport.id))
            .join(Agent, Agent.id == ExitReport.agent_id)
            .where(Agent.type == agent_type)
            .where(ExitReport.mandate_completed.is_(True))
        )
        mandate_count = (await self.session.execute(mandate_stmt)).scalar_one()

        # Total mandates (for completion rate)
        total_stmt = (
            select(func.count(ExitReport.id))
            .join(Agent, Agent.id == ExitReport.agent_id)
            .where(Agent.type == agent_type)
        )
        total_mandates = (await self.session.execute(total_stmt)).scalar_one()
        completion_rate = (
            mandate_count / total_mandates if total_mandates > 0 else 0.0
        )

        # Violation count
        violation_stmt = (
            select(func.count(Violation.id))
            .where(Violation.agent_type == agent_type)
        )
        violation_count = (await self.session.execute(violation_stmt)).scalar_one()

        # Degradation check
        degradation = await self.check_degradation(agent_type)

        # Baseline
        baseline = await self._get_baseline_obj(agent_type)
        token_efficiency_ok = True
        if baseline and baseline.token_efficiency > 0:
            # Check if recent average is within 20% of baseline
            recent_stmt = (
                select(ExitReport)
                .join(Agent, Agent.id == ExitReport.agent_id)
                .where(Agent.type == agent_type)
                .order_by(ExitReport.created_at.desc())
                .limit(5)
            )
            recent = (await self.session.execute(recent_stmt)).scalars().all()
            if recent:
                avg = sum(r.tokens_consumed for r in recent) / len(recent)
                deviation = abs(avg - baseline.token_efficiency) / baseline.token_efficiency
                token_efficiency_ok = deviation <= 0.20

        # Determine next level eligibility
        next_level = current_level + 1
        blockers: list[str] = []
        eligible = False

        if next_level == 1:
            if mandate_count < 3:
                blockers.append(f"Need 3+ completed mandates (have {mandate_count})")
            if violation_count > 0:
                blockers.append(f"Need 0 violations (have {violation_count})")
            eligible = len(blockers) == 0

        elif next_level == 2:
            if mandate_count < 10:
                blockers.append(f"Need 10+ completed mandates (have {mandate_count})")
            if not token_efficiency_ok:
                blockers.append("Token efficiency deviates >20% from baseline")
            if completion_rate < 0.85:
                blockers.append(f"Need >85% completion rate (have {completion_rate:.0%})")
            eligible = len(blockers) == 0

        elif next_level == 3:
            if mandate_count < 25:
                blockers.append(f"Need 25+ completed mandates (have {mandate_count})")
            if degradation.get("degraded"):
                blockers.append("Agent type has active degradation flags")
            blockers.append("Requires explicit Interpreter endorsement")
            eligible = False  # always requires endorsement

        elif next_level > 3:
            blockers.append("Already at maximum trust level (3 = Veteran)")
            eligible = False

        # Promotion frozen check
        frozen = await self._is_promotion_frozen(agent_type)
        if frozen:
            blockers.append("Promotion frozen due to prior violation (graduated sanctions)")
            eligible = False

        return {
            "agent_type": agent_type,
            "current_level": current_level,
            "next_level": next_level,
            "eligible_for_promotion": eligible,
            "blockers": blockers,
            "criteria": {
                "mandate_count": mandate_count,
                "total_mandates": total_mandates,
                "completion_rate": round(completion_rate, 3),
                "violation_count": violation_count,
                "degraded": degradation.get("degraded", False),
                "token_efficiency_ok": token_efficiency_ok,
            },
        }

    # -- violations ---------------------------------------------------------

    async def record_violation(
        self, agent_id: str, violation_data: dict
    ) -> dict:
        """Record a violation and apply graduated sanctions.

        1st offense: noted (no trust impact).
        2nd offense: promotion frozen.
        3rd offense: demote one level.
        """
        uid = uuid.UUID(agent_id)
        agent = await self.session.get(Agent, uid)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        violation = Violation(
            agent_id=uid,
            agent_type=agent.type,
            violation_type=violation_data.get("violation_type", "unspecified"),
            severity=violation_data.get("severity", "incidental"),
            description=violation_data.get("description", ""),
        )
        self.session.add(violation)
        await self.session.flush()

        # Count prior violations for this agent type
        count_stmt = (
            select(func.count(Violation.id))
            .where(Violation.agent_type == agent.type)
        )
        total_violations = (await self.session.execute(count_stmt)).scalar_one()

        sanction: dict[str, Any]
        if total_violations == 1:
            sanction = {
                "action": "noted",
                "description": "First violation noted. No trust impact.",
                "trust_impact": "none",
            }
        elif total_violations == 2:
            sanction = {
                "action": "warning",
                "description": "Second violation. Trust promotion frozen.",
                "trust_impact": "freeze",
            }
        else:
            # 3rd+ violation: demote
            demotion = await self.demote(agent.type, reason=f"3rd+ violation: {violation.description}")
            sanction = {
                "action": "demotion",
                "description": f"Violation #{total_violations}. Trust demoted.",
                "trust_impact": "demoted",
                "demotion_result": demotion,
            }

        return {
            "violation": {
                "id": str(violation.id),
                "agent_id": str(violation.agent_id),
                "agent_type": violation.agent_type,
                "violation_type": violation.violation_type,
                "severity": violation.severity,
                "description": violation.description,
                "timestamp": violation.timestamp.isoformat(),
            },
            "total_violations_for_type": total_violations,
            "sanction": sanction,
        }

    # -- trust promotion / demotion -----------------------------------------

    async def promote(
        self, agent_type: str, to_level: int, reason: str
    ) -> dict:
        """Promote an agent type's trust level."""
        # Get current level
        stmt = select(func.max(Agent.trust_level)).where(Agent.type == agent_type)
        current = (await self.session.execute(stmt)).scalar_one() or 0

        if to_level <= current:
            return {"error": f"Cannot promote: current level {current} >= requested {to_level}"}
        if to_level > 3:
            return {"error": "Maximum trust level is 3 (Veteran)"}

        # Update all agents of this type
        agent_stmt = select(Agent).where(Agent.type == agent_type)
        agents = (await self.session.execute(agent_stmt)).scalars().all()
        for a in agents:
            a.trust_level = to_level

        event = TrustEvent(
            agent_type=agent_type,
            from_level=current,
            to_level=to_level,
            reason=reason,
        )
        self.session.add(event)
        await self.session.flush()

        return {
            "agent_type": agent_type,
            "from_level": current,
            "to_level": to_level,
            "reason": reason,
            "agents_updated": len(agents),
            "timestamp": event.timestamp.isoformat(),
        }

    async def demote(self, agent_type: str, reason: str) -> dict:
        """Demote an agent type one trust level."""
        stmt = select(func.max(Agent.trust_level)).where(Agent.type == agent_type)
        current = (await self.session.execute(stmt)).scalar_one() or 0

        new_level = max(current - 1, 0)

        agent_stmt = select(Agent).where(Agent.type == agent_type)
        agents = (await self.session.execute(agent_stmt)).scalars().all()
        for a in agents:
            a.trust_level = new_level

        event = TrustEvent(
            agent_type=agent_type,
            from_level=current,
            to_level=new_level,
            reason=reason,
        )
        self.session.add(event)
        await self.session.flush()

        return {
            "agent_type": agent_type,
            "from_level": current,
            "to_level": new_level,
            "reason": reason,
            "agents_updated": len(agents),
            "timestamp": event.timestamp.isoformat(),
        }

    # -- internal helpers ---------------------------------------------------

    async def _get_baseline_obj(self, agent_type: str) -> Baseline | None:
        stmt = select(Baseline).where(Baseline.agent_type == agent_type)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _update_baseline(
        self,
        agent_type: str,
        *,
        tokens_consumed: int,
        mandate_completed: bool,
        report_quality: float,
    ) -> dict:
        """Update rolling-average baseline for an agent type."""
        baseline = await self._get_baseline_obj(agent_type)

        if baseline is None:
            # First baseline
            baseline = Baseline(
                agent_type=agent_type,
                token_efficiency=float(tokens_consumed),
                completion_rate=1.0 if mandate_completed else 0.0,
                exit_report_quality=report_quality,
                mandate_count=1,
            )
            self.session.add(baseline)
            await self.session.flush()
            return {
                "action": "created",
                "agent_type": agent_type,
                "baseline": _baseline_to_dict(baseline),
            }

        # Rolling average update
        n = baseline.mandate_count
        baseline.token_efficiency = (
            (baseline.token_efficiency * n + tokens_consumed) / (n + 1)
        )
        completed_count = baseline.completion_rate * n + (1.0 if mandate_completed else 0.0)
        baseline.completion_rate = completed_count / (n + 1)
        baseline.exit_report_quality = (
            (baseline.exit_report_quality * n + report_quality) / (n + 1)
        )
        baseline.mandate_count = n + 1
        baseline.last_recalculated_at = _utcnow()
        await self.session.flush()

        return {
            "action": "updated",
            "agent_type": agent_type,
            "baseline": _baseline_to_dict(baseline),
        }

    async def _is_promotion_frozen(self, agent_type: str) -> bool:
        """Promotion is frozen if the type has exactly 2 violations
        (graduated sanctions: 2nd offense = freeze).
        """
        stmt = (
            select(func.count(Violation.id))
            .where(Violation.agent_type == agent_type)
        )
        count = (await self.session.execute(stmt)).scalar_one()
        return count == 2


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _report_to_dict(report: ExitReport, quality: float) -> dict:
    return {
        "id": str(report.id),
        "agent_id": str(report.agent_id),
        "mandate_completed": report.mandate_completed,
        "key_findings": report.key_findings,
        "what_worked": report.what_worked,
        "what_failed": report.what_failed,
        "recommendations": report.recommendations,
        "gaps": report.gaps,
        "decisions": report.decisions,
        "dissent": report.dissent,
        "contrarian": report.contrarian,
        "tokens_consumed": report.tokens_consumed,
        "freshness_score": report.freshness_score,
        "quality_score": quality,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


def _baseline_to_dict(baseline: Baseline) -> dict:
    return {
        "id": str(baseline.id),
        "agent_type": baseline.agent_type,
        "token_efficiency": baseline.token_efficiency,
        "completion_rate": baseline.completion_rate,
        "exit_report_quality": baseline.exit_report_quality,
        "mandate_count": baseline.mandate_count,
        "first_recorded_at": baseline.first_recorded_at.isoformat(),
        "last_recalculated_at": baseline.last_recalculated_at.isoformat(),
    }
