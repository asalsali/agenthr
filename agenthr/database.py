"""
AgentHR Database Layer

SQLAlchemy 2.0+ async ORM models for the AgentHR platform.
Uses SQLite (aiosqlite) for development, swappable to Postgres
via the DATABASE_URL environment variable.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import JSON, Uuid


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(AsyncAttrs, DeclarativeBase):
    """Shared declarative base for all ORM models."""

    type_annotation_map = {
        dict[str, Any]: JSON,
        list[Any]: JSON,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_new_id
    )
    type: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=True
    )
    parent_ids: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    framework: Mapped[str] = mapped_column(String(128))
    definition_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    territory: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    mandate_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    mandate: Mapped[str] = mapped_column(Text)
    mandate_echo: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="onboarding"
    )
    phase: Mapped[str] = mapped_column(String(32), default="genesis")
    trust_level: Mapped[int] = mapped_column(Integer, default=0)
    born_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tokens_consumed: Mapped[int] = mapped_column(Integer, default=0)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True
    )

    # -- relationships -------------------------------------------------------
    parent: Mapped[Agent | None] = relationship(
        "Agent", remote_side=[id], back_populates="children"
    )
    children: Mapped[list[Agent]] = relationship(
        "Agent", back_populates="parent"
    )
    heartbeats: Mapped[list[Heartbeat]] = relationship(
        "Heartbeat", back_populates="agent"
    )
    exit_reports: Mapped[list[ExitReport]] = relationship(
        "ExitReport", back_populates="agent"
    )
    violations: Mapped[list[Violation]] = relationship(
        "Violation", back_populates="agent"
    )
    innate_signals: Mapped[list[InnateSignal]] = relationship(
        "InnateSignal", back_populates="agent"
    )

    def __repr__(self) -> str:
        return f"<Agent {self.name!r} ({self.status})>"


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_new_id
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=False
    )
    last_action: Mapped[str] = mapped_column(String(120))
    phase: Mapped[str] = mapped_column(String(32))
    action_count: Mapped[int] = mapped_column(Integer, default=0)
    momentum: Mapped[str] = mapped_column(String(32), default="neutral")
    tool_breakdown: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    stale_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    # -- relationships -------------------------------------------------------
    agent: Mapped[Agent] = relationship("Agent", back_populates="heartbeats")

    def __repr__(self) -> str:
        return f"<Heartbeat agent={self.agent_id} count={self.action_count}>"


class ExitReport(Base):
    __tablename__ = "exit_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_new_id
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=False
    )
    mandate_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    key_findings: Mapped[list[Any] | None] = mapped_column(
        JSON, nullable=True
    )
    what_worked: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_failed: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendations: Mapped[str | None] = mapped_column(Text, nullable=True)
    gaps: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    decisions: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    dissent: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    contrarian: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_consumed: Mapped[int] = mapped_column(Integer, default=0)
    freshness_score: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    # -- relationships -------------------------------------------------------
    agent: Mapped[Agent] = relationship("Agent", back_populates="exit_reports")

    def __repr__(self) -> str:
        return f"<ExitReport agent={self.agent_id} completed={self.mandate_completed}>"


class TrustEvent(Base):
    __tablename__ = "trust_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_new_id
    )
    agent_type: Mapped[str] = mapped_column(String(128))
    from_level: Mapped[int] = mapped_column(Integer)
    to_level: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<TrustEvent {self.agent_type} {self.from_level}->{self.to_level}>"


class Violation(Base):
    __tablename__ = "violations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_new_id
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=False
    )
    agent_type: Mapped[str] = mapped_column(String(128))
    violation_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    # -- relationships -------------------------------------------------------
    agent: Mapped[Agent] = relationship("Agent", back_populates="violations")

    def __repr__(self) -> str:
        return f"<Violation {self.violation_type} ({self.severity})>"


class Baseline(Base):
    __tablename__ = "baselines"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_new_id
    )
    agent_type: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )
    token_efficiency: Mapped[float] = mapped_column(Float, default=0.0)
    completion_rate: Mapped[float] = mapped_column(Float, default=0.0)
    exit_report_quality: Mapped[float] = mapped_column(Float, default=0.0)
    mandate_count: Mapped[int] = mapped_column(Integer, default=0)
    first_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    last_recalculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<Baseline {self.agent_type} n={self.mandate_count}>"


class InnateSignal(Base):
    __tablename__ = "innate_signals"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_new_id
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=False
    )
    signal_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    # -- relationships -------------------------------------------------------
    agent: Mapped[Agent] = relationship(
        "Agent", back_populates="innate_signals"
    )

    def __repr__(self) -> str:
        return f"<InnateSignal {self.signal_type} ({self.severity})>"


# ---------------------------------------------------------------------------
# Engine & Session Factories
# ---------------------------------------------------------------------------

_DEFAULT_URL = "sqlite+aiosqlite:///agenthr.db"

_engine = None
_session_factory = None


def get_engine(url: str | None = None):
    """
    Return (and cache) the async engine.

    Priority:
      1. Explicit ``url`` argument
      2. ``DATABASE_URL`` environment variable
      3. Local SQLite file via aiosqlite
    """
    global _engine
    if _engine is not None:
        return _engine

    resolved = url or os.environ.get("DATABASE_URL") or _DEFAULT_URL
    _engine = create_async_engine(resolved, echo=False)
    return _engine


def get_session() -> AsyncSession:
    """Return a new async session bound to the cached engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory()


async def create_tables(engine=None) -> None:
    """Create all tables. Safe to call repeatedly (uses CREATE IF NOT EXISTS)."""
    eng = engine or get_engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
