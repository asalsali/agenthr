"""
HeartbeatService -- Agent liveness tracking and anomaly detection.

Records heartbeats, detects staleness, and runs the five innate
detection patterns (territory drift, token hemorrhage, stuck loops,
role drift, output contradiction).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthr.database import Agent, Baseline, Heartbeat, InnateSignal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STALE_THRESHOLD_MINUTES = 15
_ESCALATION_WINDOW = 5  # heartbeats
_ESCALATION_SIGNAL_COUNT = 2

# Expected tool distribution per role archetype (fractions summing to ~1.0).
_ROLE_BASELINES: dict[str, dict[str, float]] = {
    "analyst": {"Read": 0.40, "Grep": 0.25, "Glob": 0.15, "Bash": 0.10, "Write": 0.05, "Edit": 0.05},
    "writer": {"Write": 0.35, "Edit": 0.30, "Read": 0.15, "Bash": 0.10, "Grep": 0.05, "Glob": 0.05},
    "executor": {"Bash": 0.45, "Write": 0.20, "Read": 0.15, "Edit": 0.10, "Grep": 0.05, "Glob": 0.05},
    "builder": {"Bash": 0.25, "Write": 0.25, "Read": 0.20, "Edit": 0.15, "Grep": 0.10, "Glob": 0.05},
}

# Token-per-action expected rate by tier
_TOKEN_RATES = {"low": 150, "medium": 400, "high": 1000}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class HeartbeatService:
    """Agent heartbeat recording, staleness detection, and anomaly checks."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # -- record -------------------------------------------------------------

    async def record(self, agent_id: str, heartbeat_data: dict) -> dict:
        """Record a heartbeat for *agent_id*.

        *heartbeat_data* should include at minimum ``last_action`` and
        ``phase``.  Optional: ``action_count``, ``momentum``,
        ``tool_breakdown``.

        Returns the persisted heartbeat dict plus any innate signals fired.
        """
        uid = uuid.UUID(agent_id)
        agent = await self.session.get(Agent, uid)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        hb = Heartbeat(
            agent_id=uid,
            last_action=heartbeat_data.get("last_action", ""),
            phase=heartbeat_data.get("phase", agent.phase),
            action_count=heartbeat_data.get("action_count", 0),
            momentum=heartbeat_data.get("momentum", "neutral"),
            tool_breakdown=heartbeat_data.get("tool_breakdown"),
            stale_since=None,
        )
        self.session.add(hb)

        # Clear staleness on the agent -- it is alive.
        # (We store stale_since on the most recent heartbeat; agent-level
        #  staleness is inferred from the latest heartbeat record.)

        await self.session.flush()

        # Innate detection
        signals = await self.detect_anomalies(agent_id, heartbeat_data)

        # Check for escalation: 2+ distinct signal types within last
        # _ESCALATION_WINDOW heartbeats.
        escalation = None
        if signals:
            recent_signals = await self._recent_signals(agent_id, window=_ESCALATION_WINDOW)
            all_types = {s["signal_type"] for s in recent_signals} | {s["signal_type"] for s in signals}
            if len(all_types) >= _ESCALATION_SIGNAL_COUNT:
                escalation = {
                    "escalated": True,
                    "signal_types": sorted(all_types),
                    "message": (
                        f"INNATE ESCALATION: agent {agent_id} fired "
                        f"{len(all_types)} distinct anomaly signals within "
                        f"{_ESCALATION_WINDOW} heartbeats."
                    ),
                }

        return {
            "heartbeat": _heartbeat_to_dict(hb),
            "signals": signals,
            "escalation": escalation,
        }

    # -- get ----------------------------------------------------------------

    async def get_latest(self, agent_id: str) -> dict | None:
        """Most recent heartbeat for an agent."""
        uid = uuid.UUID(agent_id)
        stmt = (
            select(Heartbeat)
            .where(Heartbeat.agent_id == uid)
            .order_by(Heartbeat.timestamp.desc())
            .limit(1)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _heartbeat_to_dict(row) if row else None

    async def get_history(self, agent_id: str, limit: int = 50) -> list[dict]:
        """Heartbeat history, most recent first."""
        uid = uuid.UUID(agent_id)
        stmt = (
            select(Heartbeat)
            .where(Heartbeat.agent_id == uid)
            .order_by(Heartbeat.timestamp.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_heartbeat_to_dict(r) for r in rows]

    # -- staleness ----------------------------------------------------------

    async def detect_stale(self, threshold_minutes: int = _STALE_THRESHOLD_MINUTES) -> list[dict]:
        """Find active agents whose most recent heartbeat is older than
        *threshold_minutes*.
        """
        cutoff = _utcnow() - timedelta(minutes=threshold_minutes)

        # Sub-query: latest heartbeat timestamp per agent
        latest_hb = (
            select(
                Heartbeat.agent_id,
                func.max(Heartbeat.timestamp).label("latest_ts"),
            )
            .group_by(Heartbeat.agent_id)
            .subquery()
        )

        # Join to agents that are still active
        stmt = (
            select(Agent, latest_hb.c.latest_ts)
            .outerjoin(latest_hb, Agent.id == latest_hb.c.agent_id)
            .where(Agent.status.in_(["onboarding", "active"]))
            .where(
                (latest_hb.c.latest_ts < cutoff)
                | (latest_hb.c.latest_ts.is_(None))
            )
        )
        rows = (await self.session.execute(stmt)).all()

        results: list[dict] = []
        for agent, latest_ts in rows:
            results.append({
                "agent_id": str(agent.id),
                "name": agent.name,
                "status": agent.status,
                "last_heartbeat": latest_ts.isoformat() if latest_ts else None,
                "stale_minutes": (
                    int((_utcnow() - latest_ts).total_seconds() / 60)
                    if latest_ts else None
                ),
            })
        return results

    # -- anomaly detection --------------------------------------------------

    async def detect_anomalies(
        self, agent_id: str, heartbeat: dict
    ) -> list[dict]:
        """Run five innate detection patterns.  Returns a list of signal
        dicts (may be empty).
        """
        uid = uuid.UUID(agent_id)
        agent = await self.session.get(Agent, uid)
        if agent is None:
            return []

        signals: list[dict] = []

        # 1. Territory drift
        sig = self._check_territory_drift(agent, heartbeat)
        if sig:
            signals.append(sig)

        # 2. Token hemorrhage
        sig = await self._check_token_hemorrhage(agent, heartbeat)
        if sig:
            signals.append(sig)

        # 3. Stuck loop
        sig = await self._check_stuck_loop(uid, heartbeat)
        if sig:
            signals.append(sig)

        # 4. Role drift
        sig = self._check_role_drift(agent, heartbeat)
        if sig:
            signals.append(sig)

        # 5. Output contradiction (simplified)
        sig = self._check_output_contradiction(agent, heartbeat)
        if sig:
            signals.append(sig)

        # Persist any signals
        for s in signals:
            self.session.add(InnateSignal(
                agent_id=uid,
                signal_type=s["signal_type"],
                description=s["description"],
                severity=s["severity"],
            ))
        if signals:
            await self.session.flush()

        return signals

    # -- innate pattern implementations -------------------------------------

    @staticmethod
    def _check_territory_drift(agent: Agent, heartbeat: dict) -> dict | None:
        """Signal if last_action mentions a path outside the agent's territory."""
        territory = agent.territory
        if not territory:
            return None
        last_action: str = heartbeat.get("last_action", "")
        if not last_action:
            return None
        # Simple heuristic: if the action contains a file-path-like string,
        # check whether any territory glob prefix matches.
        for glob_pat in territory:
            prefix = glob_pat.replace("**", "").replace("*", "").rstrip("/")
            if prefix and prefix in last_action:
                return None  # within territory
        # If none matched and the action looks like a file write, signal.
        write_keywords = {"write", "edit", "create", "wrote", "modified"}
        if any(kw in last_action.lower() for kw in write_keywords):
            return {
                "signal_type": "territory_drift",
                "description": f"Action outside territory: {last_action[:100]}",
                "severity": "warning",
            }
        return None

    @staticmethod
    async def _check_token_hemorrhage(agent: Agent, heartbeat: dict) -> dict | None:
        """Signal if token consumption is 3x the expected rate."""
        action_count = heartbeat.get("action_count", 0)
        if action_count < 5:
            return None  # too few data points
        tokens = agent.tokens_consumed or 0
        if tokens == 0:
            return None
        expected_per_action = _TOKEN_RATES.get("medium", 400)
        expected_total = expected_per_action * action_count
        if tokens > expected_total * 3:
            return {
                "signal_type": "token_hemorrhage",
                "description": (
                    f"Tokens consumed ({tokens}) is >{3}x expected "
                    f"({expected_total}) after {action_count} actions."
                ),
                "severity": "warning",
            }
        return None

    async def _check_stuck_loop(
        self, agent_uuid: uuid.UUID, heartbeat: dict
    ) -> dict | None:
        """Signal if the last 3 heartbeats have identical last_action."""
        current_action = heartbeat.get("last_action", "")
        if not current_action:
            return None
        stmt = (
            select(Heartbeat.last_action)
            .where(Heartbeat.agent_id == agent_uuid)
            .order_by(Heartbeat.timestamp.desc())
            .limit(2)
        )
        prev_actions = (await self.session.execute(stmt)).scalars().all()
        if len(prev_actions) < 2:
            return None
        if all(a == current_action for a in prev_actions):
            return {
                "signal_type": "stuck_loop",
                "description": (
                    f"3 consecutive identical actions: {current_action[:80]}"
                ),
                "severity": "warning",
            }
        return None

    @staticmethod
    def _check_role_drift(agent: Agent, heartbeat: dict) -> dict | None:
        """Signal if tool_breakdown diverges >40% from type baseline."""
        breakdown = heartbeat.get("tool_breakdown")
        if not breakdown:
            return None
        base_type = agent.type.lower().split("-")[0].split("_")[0]
        expected = _ROLE_BASELINES.get(base_type)
        if not expected:
            return None
        # Normalise breakdown to fractions
        total = sum(breakdown.values()) or 1
        actual = {k: v / total for k, v in breakdown.items()}
        # Compute divergence (sum of absolute differences / 2)
        all_keys = set(expected) | set(actual)
        divergence = sum(
            abs(expected.get(k, 0.0) - actual.get(k, 0.0)) for k in all_keys
        ) / 2.0
        if divergence > 0.4:
            return {
                "signal_type": "role_drift",
                "description": (
                    f"Tool distribution diverges {divergence:.0%} from "
                    f"{base_type} baseline."
                ),
                "severity": "warning",
            }
        return None

    @staticmethod
    def _check_output_contradiction(agent: Agent, heartbeat: dict) -> dict | None:
        """Simplified contradiction check: high action count + diverging momentum."""
        action_count = heartbeat.get("action_count", 0)
        momentum = heartbeat.get("momentum", "neutral")
        if action_count > 20 and momentum == "diverging":
            return {
                "signal_type": "output_contradiction",
                "description": (
                    f"Agent has {action_count} actions but momentum is "
                    f"diverging -- possible output contradiction."
                ),
                "severity": "warning",
            }
        return None

    # -- internal helpers ---------------------------------------------------

    async def _recent_signals(
        self, agent_id: str, *, window: int = _ESCALATION_WINDOW
    ) -> list[dict]:
        """Get innate signals from the last *window* heartbeats."""
        uid = uuid.UUID(agent_id)
        # Get the timestamp of the Nth most recent heartbeat
        ts_stmt = (
            select(Heartbeat.timestamp)
            .where(Heartbeat.agent_id == uid)
            .order_by(Heartbeat.timestamp.desc())
            .limit(window)
        )
        timestamps = (await self.session.execute(ts_stmt)).scalars().all()
        if not timestamps:
            return []
        cutoff = min(timestamps)
        sig_stmt = (
            select(InnateSignal)
            .where(InnateSignal.agent_id == uid)
            .where(InnateSignal.timestamp >= cutoff)
            .order_by(InnateSignal.timestamp.desc())
        )
        rows = (await self.session.execute(sig_stmt)).scalars().all()
        return [
            {
                "signal_type": s.signal_type,
                "description": s.description,
                "severity": s.severity,
                "timestamp": s.timestamp.isoformat(),
            }
            for s in rows
        ]


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------

def _heartbeat_to_dict(hb: Heartbeat) -> dict:
    return {
        "id": str(hb.id),
        "agent_id": str(hb.agent_id),
        "last_action": hb.last_action,
        "phase": hb.phase,
        "action_count": hb.action_count,
        "momentum": hb.momentum,
        "tool_breakdown": hb.tool_breakdown,
        "stale_since": hb.stale_since.isoformat() if hb.stale_since else None,
        "timestamp": hb.timestamp.isoformat() if hb.timestamp else None,
    }
