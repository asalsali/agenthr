"""Shared test fixtures — in-memory SQLite DB for each test."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agenthr.database import Base, Agent


@pytest_asyncio.fixture
async def session():
    """Yield a fresh async session backed by an in-memory SQLite DB."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess

    await engine.dispose()


@pytest_asyncio.fixture
async def sample_agent(session: AsyncSession) -> Agent:
    """Insert and return a basic active agent."""
    agent = Agent(
        type="analyst",
        name="test-agent",
        framework="custom",
        mandate="Analyze test data",
        status="active",
        phase="research",
        tokens_consumed=500,
        territory=["src/data/**"],
    )
    session.add(agent)
    await session.flush()
    return agent
