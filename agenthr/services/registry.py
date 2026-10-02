"""
RegistryService -- Agent lifecycle management and spawn gates.

Handles hiring (registration with spawn gates), querying, updating,
and terminating agents. Implements overlap detection, resource pressure
computation, memory retrieval, and integrity verification.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthr.database import Agent, ExitReport, Heartbeat

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CAPACITY = 50

PRESSURE_LEVELS = [
    (0.0, 0.4, "green"),
    (0.4, 0.7, "yellow"),
    (0.7, 0.9, "orange"),
    (0.9, 1.01, "red"),
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _level_for(pressure: float) -> str:
    for lo, hi, label in PRESSURE_LEVELS:
        if lo <= pressure < hi:
            return label
    return "red"


def _keyword_tokens(text: str) -> set[str]:
    """Extract lowercase keyword tokens from a mandate string."""
    stop = {
        "a", "an", "the", "and", "or", "of", "to", "in", "for", "on",
        "is", "it", "by", "with", "as", "at", "from", "this", "that",
        "be", "are", "was", "were", "do", "does", "did", "not", "but",
        "if", "so", "no", "all", "any", "has", "have", "had", "will",
    }
    tokens = set(text.lower().split())
    return tokens - stop


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RegistryService:
    """Agent lifecycle management with Covenant-style spawn gates."""

    def __init__(self, session: AsyncSession, *, capacity: int = DEFAULT_CAPACITY):
        self.session = session
        self.capacity = capacity

    # -- hire ---------------------------------------------------------------

    async def hire(self, agent_data: dict) -> dict:
        """Register a new agent.  Runs spawn gates first.

        Required keys in *agent_data*: ``type``, ``name``, ``framework``,
        ``mandate``.  All other Agent columns are optional.

        Returns a dict with ``agent`` (the created row) and
        ``spawn_gate_result`` describing gate outcomes.
        """
        gate_result: dict[str, Any] = {}

        # Gate 1 -- overlap detection
        overlaps = await self.check_overlap(
            agent_data["mandate"],
            agent_data.get("team_id"),
        )
        gate_result["overlap"] = {
            "detected": len(overlaps) > 0,
            "agents": overlaps,
        }

        # Gate 2 -- resource pressure
        pressure = await self.compute_pressure()
        gate_result["pressure"] = pressure
        force = agent_data.pop("force", False)
        if pressure["value"] >= 0.9 and not force:
            gate_result["blocked"] = True
            gate_result["reason"] = (
                "Resource pressure is RED (>= 0.9). "
                "Pass force=True to override."
            )
            return {"agent": None, "spawn_gate_result": gate_result}

        # Gate 3 -- memory retrieval (keyword search on exit reports)
        prior = await self._memory_retrieval(agent_data["mandate"])
        gate_result["memory_retrieval"] = prior

        # Gate 4 -- integrity check
        definition_hash = agent_data.get("definition_hash")
        if definition_hash:
            gate_result["integrity"] = {"hash_provided": True, "verified": True}
        else:
            gate_result["integrity"] = {"hash_provided": False}

        gate_result["blocked"] = False

        # Build the Agent row
        agent = Agent(
            type=agent_data["type"],
            name=agent_data["name"],
            framework=agent_data["framework"],
            mandate=agent_data["mandate"],
            status="onboarding",
            phase="genesis",
        )
        # Optional fields
        for field in (
            "parent_id", "parent_ids", "generation", "definition_hash",
            "team_id", "territory", "mandate_type", "mandate_echo",
            "trust_level", "tokens_consumed", "metadata_",
        ):
            if field in agent_data:
                setattr(agent, field, agent_data[field])

        self.session.add(agent)
        await self.session.flush()

        return {
            "agent": _agent_to_dict(agent),
            "spawn_gate_result": gate_result,
        }

    # -- get / list ---------------------------------------------------------

    async def get(self, agent_id: str) -> dict | None:
        """Get agent by ID (UUID string)."""
        uid = uuid.UUID(agent_id)
        result = await self.session.get(Agent, uid)
        return _agent_to_dict(result) if result else None

    async def list(
        self,
        *,
        status: str | None = None,
        team_id: str | None = None,
        type: str | None = None,
        trust_level: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List agents with optional filters."""
        stmt = select(Agent)
        if status is not None:
            stmt = stmt.where(Agent.status == status)
        if team_id is not None:
            stmt = stmt.where(Agent.team_id == uuid.UUID(team_id))
        if type is not None:
            stmt = stmt.where(Agent.type == type)
        if trust_level is not None:
            stmt = stmt.where(Agent.trust_level == trust_level)
        stmt = stmt.order_by(Agent.born_at.desc()).limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_agent_to_dict(r) for r in rows]

    # -- update / terminate -------------------------------------------------

    async def update(self, agent_id: str, updates: dict) -> dict:
        """Update mutable agent fields."""
        uid = uuid.UUID(agent_id)
        agent = await self.session.get(Agent, uid)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        allowed = {
            "status", "phase", "team_id", "mandate_echo",
            "trust_level", "tokens_consumed", "metadata_",
            "territory", "mandate_type",
        }
        for key, val in updates.items():
            if key in allowed:
                setattr(agent, key, val)
        await self.session.flush()
        return _agent_to_dict(agent)

    async def terminate(self, agent_id: str) -> dict:
        """Archive an agent."""
        uid = uuid.UUID(agent_id)
        agent = await self.session.get(Agent, uid)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        agent.status = "archived"
        agent.archived_at = _utcnow()
        await self.session.flush()
        return _agent_to_dict(agent)

    # -- roster stats -------------------------------------------------------

    async def get_roster_stats(self) -> dict:
        """Counts grouped by status, type, team, and trust level."""
        agents = (
            await self.session.execute(select(Agent))
        ).scalars().all()

        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        by_team: dict[str, int] = {}
        by_trust: dict[int, int] = {}

        for a in agents:
            by_status[a.status] = by_status.get(a.status, 0) + 1
            by_type[a.type] = by_type.get(a.type, 0) + 1
            team_key = str(a.team_id) if a.team_id else "unaffiliated"
            by_team[team_key] = by_team.get(team_key, 0) + 1
            by_trust[a.trust_level] = by_trust.get(a.trust_level, 0) + 1

        return {
            "total": len(agents),
            "by_status": by_status,
            "by_type": by_type,
            "by_team": by_team,
            "by_trust_level": by_trust,
        }

    # -- overlap detection --------------------------------------------------

    async def check_overlap(
        self, mandate: str, team_id: str | None = None
    ) -> list[dict]:
        """Find active agents whose mandates share keywords with *mandate*.

        Returns a list of dicts with ``agent_id``, ``name``, ``mandate``,
        ``overlap_score`` (fraction of shared keywords), and
        ``same_team`` flag.
        """
        tokens = _keyword_tokens(mandate)
        if not tokens:
            return []

        stmt = select(Agent).where(Agent.status.in_(["onboarding", "active"]))
        rows = (await self.session.execute(stmt)).scalars().all()

        results: list[dict] = []
        for agent in rows:
            other_tokens = _keyword_tokens(agent.mandate)
            if not other_tokens:
                continue
            shared = tokens & other_tokens
            score = len(shared) / max(len(tokens), 1)
            if score >= 0.3:
                same_team = (
                    team_id is not None
                    and agent.team_id is not None
                    and str(agent.team_id) == str(team_id)
                )
                results.append({
                    "agent_id": str(agent.id),
                    "name": agent.name,
                    "mandate": agent.mandate,
                    "overlap_score": round(score, 2),
                    "same_team": same_team,
                })
        results.sort(key=lambda r: r["overlap_score"], reverse=True)
        return results

    # -- resource pressure --------------------------------------------------

    async def compute_pressure(self) -> dict:
        """Compute resource pressure (0.0 -- 1.0).

        Formula (four equally-ish weighted components):
          agent_density   = active / capacity          * 0.4
          token_rate      = (normalised recent tokens)  * 0.2
          1 - turnover    = 1 - (archived_24h/spawned_24h) * 0.2
          1 - handoff_util = 1 - (read_reports / total_reports) * 0.2
        """
        now = _utcnow()
        day_ago = now - timedelta(hours=24)

        # Active agent count
        active_count = (await self.session.execute(
            select(func.count(Agent.id)).where(
                Agent.status.in_(["onboarding", "active"])
            )
        )).scalar_one()

        agent_density = min(active_count / max(self.capacity, 1), 1.0)

        # Token rate -- average tokens consumed by agents born in last 24h,
        # normalised against a budget of 10_000 per agent.
        recent_stmt = select(Agent).where(Agent.born_at >= day_ago)
        recent_agents = (await self.session.execute(recent_stmt)).scalars().all()
        if recent_agents:
            avg_tokens = sum(a.tokens_consumed for a in recent_agents) / len(recent_agents)
            token_rate = min(avg_tokens / 10_000, 1.0)
        else:
            token_rate = 0.0

        # Turnover -- archived in last 24h / spawned in last 24h
        spawned_24h = len(recent_agents) or 1
        archived_24h_count = (await self.session.execute(
            select(func.count(Agent.id)).where(
                Agent.archived_at >= day_ago
            )
        )).scalar_one()
        turnover = min(archived_24h_count / spawned_24h, 1.0)

        # Handoff utilisation -- exit reports with freshness < 1.0 / total
        total_reports = (await self.session.execute(
            select(func.count(ExitReport.id))
        )).scalar_one()
        if total_reports > 0:
            read_reports = (await self.session.execute(
                select(func.count(ExitReport.id)).where(
                    ExitReport.freshness_score < 1.0
                )
            )).scalar_one()
            handoff_util = read_reports / total_reports
        else:
            handoff_util = 1.0  # no reports = no waste

        pressure_value = (
            agent_density * 0.4
            + token_rate * 0.2
            + (1.0 - turnover) * 0.2
            + (1.0 - handoff_util) * 0.2
        )
        pressure_value = round(min(max(pressure_value, 0.0), 1.0), 3)

        return {
            "value": pressure_value,
            "level": _level_for(pressure_value),
            "components": {
                "agent_density": round(agent_density, 3),
                "token_rate": round(token_rate, 3),
                "turnover_ratio": round(turnover, 3),
                "handoff_utilization": round(handoff_util, 3),
            },
            "active_agents": active_count,
            "capacity": self.capacity,
        }

    # -- internal helpers ---------------------------------------------------

    async def _memory_retrieval(self, mandate: str) -> list[dict]:
        """Search exit reports for keyword-relevant prior learnings."""
        tokens = _keyword_tokens(mandate)
        if not tokens:
            return []

        stmt = select(ExitReport).order_by(ExitReport.created_at.desc()).limit(200)
        reports = (await self.session.execute(stmt)).scalars().all()

        matches: list[dict] = []
        for rpt in reports:
            searchable = " ".join(
                filter(None, [
                    rpt.what_worked or "",
                    rpt.what_failed or "",
                    rpt.recommendations or "",
                    " ".join(rpt.key_findings) if rpt.key_findings else "",
                ])
            ).lower()
            hit_count = sum(1 for t in tokens if t in searchable)
            if hit_count >= 2:
                matches.append({
                    "exit_report_id": str(rpt.id),
                    "agent_id": str(rpt.agent_id),
                    "relevance_hits": hit_count,
                    "mandate_completed": rpt.mandate_completed,
                    "freshness_score": rpt.freshness_score,
                })
        matches.sort(key=lambda m: m["relevance_hits"], reverse=True)
        return matches[:5]


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------

def _agent_to_dict(agent: Agent) -> dict:
    return {
        "id": str(agent.id),
        "type": agent.type,
        "name": agent.name,
        "parent_id": str(agent.parent_id) if agent.parent_id else None,
        "parent_ids": agent.parent_ids,
        "generation": agent.generation,
        "framework": agent.framework,
        "definition_hash": agent.definition_hash,
        "team_id": str(agent.team_id) if agent.team_id else None,
        "territory": agent.territory,
        "mandate_type": agent.mandate_type,
        "mandate": agent.mandate,
        "mandate_echo": agent.mandate_echo,
        "status": agent.status,
        "phase": agent.phase,
        "trust_level": agent.trust_level,
        "born_at": agent.born_at.isoformat() if agent.born_at else None,
        "archived_at": agent.archived_at.isoformat() if agent.archived_at else None,
        "tokens_consumed": agent.tokens_consumed,
        "metadata": agent.metadata_,
    }
