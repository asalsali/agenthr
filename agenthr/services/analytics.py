"""
AnalyticsService -- System-wide health metrics, hotspot detection,
futility analysis, and per-team/per-type reporting.

Computes the five Covenant vital signs, identifies high-connectivity
agent type hotspots, flags futile mandates, and generates team and
agent-type reports.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthr.database import (
    Agent,
    Baseline,
    ExitReport,
    TrustEvent,
    Violation,
)
from agenthr.services.registry import RegistryService

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HOTSPOT_FAN_OUT = 4
_HOTSPOT_FAN_IN = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AnalyticsService:
    """System-wide analytics and health metrics."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # -- vital signs --------------------------------------------------------

    async def vital_signs(self) -> dict:
        """Compute five system vital signs plus global pressure.

        1. turnover_ratio (healthy: 0.6 -- 1.0)
        2. token_efficiency_trend (healthy: within 30% of baseline)
        3. handoff_utilization (healthy: above 50%)
        4. team_memory_freshness (healthy: under 7 days)
        5. output_consumption (healthy: above 30%)
        """
        now = _utcnow()
        day_ago = now - timedelta(hours=24)

        # 1. Turnover ratio
        spawned_24h = (await self.session.execute(
            select(func.count(Agent.id)).where(Agent.born_at >= day_ago)
        )).scalar_one()
        archived_24h = (await self.session.execute(
            select(func.count(Agent.id)).where(Agent.archived_at >= day_ago)
        )).scalar_one()
        turnover_ratio = (
            round(archived_24h / spawned_24h, 3)
            if spawned_24h > 0 else 0.0
        )
        turnover_healthy = 0.6 <= turnover_ratio <= 1.0

        # 2. Token efficiency trend
        # Compare average tokens per completed mandate (last 10 reports)
        # against the global average baseline.
        recent_reports_stmt = (
            select(ExitReport)
            .where(ExitReport.mandate_completed.is_(True))
            .order_by(ExitReport.created_at.desc())
            .limit(10)
        )
        recent_reports = (await self.session.execute(recent_reports_stmt)).scalars().all()
        recent_avg_tokens = (
            sum(r.tokens_consumed for r in recent_reports) / len(recent_reports)
            if recent_reports else 0.0
        )

        baselines = (await self.session.execute(select(Baseline))).scalars().all()
        global_baseline_tokens = (
            sum(b.token_efficiency for b in baselines) / len(baselines)
            if baselines else 0.0
        )
        token_deviation = (
            abs(recent_avg_tokens - global_baseline_tokens) / max(global_baseline_tokens, 1)
            if global_baseline_tokens > 0 else 0.0
        )
        token_healthy = token_deviation <= 0.30

        # 3. Handoff utilization
        total_reports = (await self.session.execute(
            select(func.count(ExitReport.id))
        )).scalar_one()
        read_reports = (await self.session.execute(
            select(func.count(ExitReport.id)).where(ExitReport.freshness_score < 1.0)
        )).scalar_one()
        handoff_util = (
            round(read_reports / total_reports, 3)
            if total_reports > 0 else 1.0
        )
        handoff_healthy = handoff_util >= 0.50

        # 4. Team memory freshness
        # Average days since each team's most recently active agent was born.
        team_freshness_stmt = (
            select(
                Agent.team_id,
                func.max(Agent.born_at).label("latest_born"),
            )
            .where(Agent.team_id.isnot(None))
            .group_by(Agent.team_id)
        )
        team_rows = (await self.session.execute(team_freshness_stmt)).all()
        if team_rows:
            ages = [
                (now - row.latest_born).total_seconds() / 86400
                for row in team_rows
                if row.latest_born is not None
            ]
            avg_team_age = round(sum(ages) / len(ages), 1) if ages else 0.0
        else:
            avg_team_age = 0.0
        team_freshness_healthy = avg_team_age < 7.0

        # 5. Output consumption
        total_archived = (await self.session.execute(
            select(func.count(Agent.id)).where(Agent.status == "archived")
        )).scalar_one()
        consumed_reports = read_reports  # reports that were referenced
        output_consumption = (
            round(consumed_reports / max(total_archived, 1), 3)
        )
        output_healthy = output_consumption >= 0.30

        # Global pressure via RegistryService
        registry_svc = RegistryService(self.session)
        pressure = await registry_svc.compute_pressure()

        return {
            "vital_signs": {
                "turnover_ratio": {
                    "value": turnover_ratio,
                    "healthy_range": "0.6-1.0",
                    "healthy": turnover_healthy,
                    "detail": {
                        "spawned_24h": spawned_24h,
                        "archived_24h": archived_24h,
                    },
                },
                "token_efficiency_trend": {
                    "value": round(recent_avg_tokens, 1),
                    "baseline": round(global_baseline_tokens, 1),
                    "deviation": round(token_deviation, 3),
                    "healthy_range": "within 30% of baseline",
                    "healthy": token_healthy,
                },
                "handoff_utilization": {
                    "value": handoff_util,
                    "healthy_range": "above 0.50",
                    "healthy": handoff_healthy,
                    "detail": {
                        "total_reports": total_reports,
                        "read_reports": read_reports,
                    },
                },
                "team_memory_freshness": {
                    "value": avg_team_age,
                    "unit": "days",
                    "healthy_range": "under 7",
                    "healthy": team_freshness_healthy,
                },
                "output_consumption": {
                    "value": output_consumption,
                    "healthy_range": "above 0.30",
                    "healthy": output_healthy,
                    "detail": {
                        "total_archived": total_archived,
                        "consumed_reports": consumed_reports,
                    },
                },
            },
            "global_pressure": pressure["value"],
            "pressure_level": pressure["level"],
        }

    # -- hotspot detection --------------------------------------------------

    async def hotspots(self) -> list[dict]:
        """Find agent types with high connectivity.

        fan_out: count of children spawned by agents of this type.
        fan_in:  count of exit reports from agents whose parent is this type.

        Hotspot: fan_out >= 4 AND fan_in >= 3.
        """
        # fan_out: for each agent type, count agents that have parent_id
        # pointing to an agent of that type.
        all_agents = (await self.session.execute(select(Agent))).scalars().all()

        # Build parent type map
        agent_map: dict[str, Agent] = {str(a.id): a for a in all_agents}
        type_fan_out: dict[str, int] = {}
        type_fan_in: dict[str, int] = {}
        type_teams: dict[str, set[str]] = {}

        for a in all_agents:
            if a.parent_id and str(a.parent_id) in agent_map:
                parent = agent_map[str(a.parent_id)]
                type_fan_out[parent.type] = type_fan_out.get(parent.type, 0) + 1

        # fan_in: for each agent type, count exit reports whose agent's
        # decisions reference an agent of that type.  Simplified: count
        # exit reports from agents whose parent type = this type.
        reports = (await self.session.execute(select(ExitReport))).scalars().all()
        for rpt in reports:
            agent_id_str = str(rpt.agent_id)
            if agent_id_str in agent_map:
                agent = agent_map[agent_id_str]
                if agent.parent_id and str(agent.parent_id) in agent_map:
                    parent = agent_map[str(agent.parent_id)]
                    type_fan_in[parent.type] = type_fan_in.get(parent.type, 0) + 1

        # Cross-domain touches
        for a in all_agents:
            t = a.type
            team_key = str(a.team_id) if a.team_id else "none"
            if t not in type_teams:
                type_teams[t] = set()
            type_teams[t].add(team_key)

        # Assemble results
        all_types = set(type_fan_out) | set(type_fan_in)
        results: list[dict] = []
        for t in all_types:
            fo = type_fan_out.get(t, 0)
            fi = type_fan_in.get(t, 0)
            cross = len(type_teams.get(t, set()))

            if fo >= _HOTSPOT_FAN_OUT and fi >= _HOTSPOT_FAN_IN:
                risk = "high"
            elif fo >= _HOTSPOT_FAN_OUT or fi >= _HOTSPOT_FAN_IN:
                risk = "watch"
            else:
                continue  # not a hotspot

            results.append({
                "agent_type": t,
                "fan_out": fo,
                "fan_in": fi,
                "cross_domain_touches": cross,
                "risk_level": risk,
                "recommendation": (
                    "Consider splitting responsibilities"
                    if risk == "high"
                    else "Monitor for increasing connectivity"
                ),
            })

        results.sort(key=lambda r: (r["risk_level"] == "high", r["fan_out"]), reverse=True)
        return results

    # -- futility report ----------------------------------------------------

    async def futility_report(self) -> list[dict]:
        """Find agents whose completed mandates were never consumed.

        An exit report with freshness_score still at 1.0 (never read)
        and created more than 24h ago is a futility candidate.
        """
        cutoff = _utcnow() - timedelta(hours=24)
        stmt = (
            select(ExitReport)
            .where(ExitReport.freshness_score >= 1.0)
            .where(ExitReport.mandate_completed.is_(True))
            .where(ExitReport.created_at < cutoff)
            .order_by(ExitReport.created_at.asc())
        )
        reports = (await self.session.execute(stmt)).scalars().all()

        results: list[dict] = []
        for rpt in reports:
            agent = await self.session.get(Agent, rpt.agent_id)
            results.append({
                "exit_report_id": str(rpt.id),
                "agent_id": str(rpt.agent_id),
                "agent_name": agent.name if agent else "unknown",
                "agent_type": agent.type if agent else "unknown",
                "mandate": agent.mandate if agent else "unknown",
                "tokens_consumed": rpt.tokens_consumed,
                "created_at": rpt.created_at.isoformat(),
                "freshness_score": rpt.freshness_score,
                "days_since_created": round(
                    (_utcnow() - rpt.created_at).total_seconds() / 86400, 1
                ),
            })
        return results

    # -- team health --------------------------------------------------------

    async def team_health(self, team_id: str) -> dict:
        """Per-team analytics."""
        tid = uuid.UUID(team_id)

        # Active agents
        active_stmt = select(Agent).where(
            Agent.team_id == tid,
            Agent.status.in_(["onboarding", "active"]),
        )
        active = (await self.session.execute(active_stmt)).scalars().all()

        # Completed mandates
        completed_stmt = (
            select(func.count(ExitReport.id))
            .join(Agent, Agent.id == ExitReport.agent_id)
            .where(Agent.team_id == tid)
            .where(ExitReport.mandate_completed.is_(True))
        )
        completed_count = (await self.session.execute(completed_stmt)).scalar_one()

        # Average token efficiency
        token_stmt = (
            select(func.avg(ExitReport.tokens_consumed))
            .join(Agent, Agent.id == ExitReport.agent_id)
            .where(Agent.team_id == tid)
        )
        avg_tokens = (await self.session.execute(token_stmt)).scalar_one() or 0.0

        # Trust distribution
        trust_stmt = (
            select(Agent.trust_level, func.count(Agent.id))
            .where(Agent.team_id == tid)
            .group_by(Agent.trust_level)
        )
        trust_rows = (await self.session.execute(trust_stmt)).all()
        trust_dist = {level: count for level, count in trust_rows}

        # Degradation flags
        type_stmt = (
            select(Agent.type)
            .where(Agent.team_id == tid)
            .distinct()
        )
        types = (await self.session.execute(type_stmt)).scalars().all()
        degraded_types: list[str] = []
        for agent_type in types:
            baseline = (await self.session.execute(
                select(Baseline).where(Baseline.agent_type == agent_type)
            )).scalar_one_or_none()
            if baseline and baseline.mandate_count >= 3:
                # Quick degradation check
                recent = (await self.session.execute(
                    select(ExitReport)
                    .join(Agent, Agent.id == ExitReport.agent_id)
                    .where(Agent.type == agent_type)
                    .order_by(ExitReport.created_at.desc())
                    .limit(5)
                )).scalars().all()
                if recent:
                    avg = sum(r.tokens_consumed for r in recent) / len(recent)
                    if baseline.token_efficiency > 0:
                        dev = abs(avg - baseline.token_efficiency) / baseline.token_efficiency
                        if dev > 0.30:
                            degraded_types.append(agent_type)

        return {
            "team_id": team_id,
            "active_agents": len(active),
            "completed_mandates": completed_count,
            "avg_token_efficiency": round(avg_tokens, 1),
            "trust_distribution": trust_dist,
            "degraded_types": degraded_types,
        }

    # -- agent type report --------------------------------------------------

    async def agent_type_report(self, agent_type: str) -> dict:
        """Full report on an agent type."""

        # Baseline
        baseline_obj = (await self.session.execute(
            select(Baseline).where(Baseline.agent_type == agent_type)
        )).scalar_one_or_none()
        baseline = (
            {
                "token_efficiency": baseline_obj.token_efficiency,
                "completion_rate": baseline_obj.completion_rate,
                "exit_report_quality": baseline_obj.exit_report_quality,
                "mandate_count": baseline_obj.mandate_count,
            }
            if baseline_obj else None
        )

        # Trust level
        trust_stmt = select(func.max(Agent.trust_level)).where(Agent.type == agent_type)
        trust_level = (await self.session.execute(trust_stmt)).scalar_one() or 0

        # Mandate count
        mandate_stmt = (
            select(func.count(ExitReport.id))
            .join(Agent, Agent.id == ExitReport.agent_id)
            .where(Agent.type == agent_type)
        )
        mandate_count = (await self.session.execute(mandate_stmt)).scalar_one()

        # Degradation
        from agenthr.services.evaluate import EvaluateService
        eval_svc = EvaluateService(self.session)
        degradation = await eval_svc.check_degradation(agent_type)

        # Promotion history
        trust_events_stmt = (
            select(TrustEvent)
            .where(TrustEvent.agent_type == agent_type)
            .order_by(TrustEvent.timestamp.desc())
            .limit(20)
        )
        trust_events = (await self.session.execute(trust_events_stmt)).scalars().all()

        # Violation history
        violation_stmt = (
            select(Violation)
            .where(Violation.agent_type == agent_type)
            .order_by(Violation.timestamp.desc())
            .limit(20)
        )
        violations = (await self.session.execute(violation_stmt)).scalars().all()

        return {
            "agent_type": agent_type,
            "baseline": baseline,
            "trust_level": trust_level,
            "mandate_count": mandate_count,
            "degradation": degradation,
            "trust_history": [
                {
                    "from_level": e.from_level,
                    "to_level": e.to_level,
                    "reason": e.reason,
                    "timestamp": e.timestamp.isoformat(),
                }
                for e in trust_events
            ],
            "violation_history": [
                {
                    "violation_type": v.violation_type,
                    "severity": v.severity,
                    "description": v.description,
                    "timestamp": v.timestamp.isoformat(),
                }
                for v in violations
            ],
        }
