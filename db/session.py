"""
Async SQLAlchemy session factory.

Usage in FastAPI:
    async with get_session() as session:
        result = await session.execute(...)
"""

import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://keyscan:keyscan_dev@localhost:5432/keyscan",
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"timeout": 8},   # 8s max — prevents startup hang if DB unreachable
)

_SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncSession:
    """Yield an async database session, rolling back on exception."""
    async with _SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
