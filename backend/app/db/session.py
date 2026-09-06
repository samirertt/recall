"""Engine/session construction.

Deliberately lazy (created on first use via lru_cache, not as an import-time side
effect) so tests can point ENGMEM_DATABASE_PATH at a temp file and get a fresh engine,
and so the FastAPI app creates its engine during startup rather than at import time.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def build_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url, pool_pre_ping=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    return build_engine(settings.database_url)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def reset_engine_cache() -> None:
    """Test-only: call after changing ENGMEM_DATABASE_PATH + Settings.cache_clear().
    Disposes the outgoing engine first — otherwise aiosqlite's background worker
    thread can outlive the test's event loop and log a harmless but noisy
    "Event loop is closed" warning during later garbage collection."""
    try:
        engine = get_engine()
    except Exception:
        engine = None
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    if engine is not None:
        await engine.dispose()


def is_fts5_available() -> bool:
    """Startup capability check — lexical search must degrade, never crash, if absent."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    try:
        options = {row[0] for row in conn.execute("PRAGMA compile_options").fetchall()}
        return "ENABLE_FTS5" in options
    finally:
        conn.close()
