import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session_factory, reset_engine_cache

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest_asyncio.fixture
async def configured_db(test_db_path: Path) -> AsyncIterator[Path]:
    """Point the app at a fresh temp SQLite file and run real Alembic migrations
    against it — this exercises the actual migration chain per test, not just a
    Base.metadata.create_all() shortcut (docs/RESEARCH.md § Backup/Migrations)."""
    os.environ["ENGMEM_DATABASE_PATH"] = str(test_db_path)
    os.environ["ENGMEM_ATTACHMENTS_DIR"] = str(test_db_path.parent / "attachments")
    get_settings.cache_clear()
    await reset_engine_cache()

    # alembic's own env.py calls asyncio.run() internally, which cannot be invoked
    # from within a running event loop — this fixture is itself a coroutine (needs
    # to `await reset_engine_cache()`), so the sync alembic call must run in its own
    # thread rather than directly in the fixture's event loop.
    alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    yield test_db_path

    await reset_engine_cache()
    get_settings.cache_clear()
    os.environ.pop("ENGMEM_DATABASE_PATH", None)
    os.environ.pop("ENGMEM_ATTACHMENTS_DIR", None)


@pytest_asyncio.fixture
async def db_session(configured_db: Path) -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


@pytest_asyncio.fixture
async def client(configured_db: Path) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
