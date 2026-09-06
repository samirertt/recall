"""Migration chain + FTS5 sync tests (docs/RESEARCH.md § SQLite & FTS5, § Backup/Migrations)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_upgrade_creates_all_tables(configured_db: Path):
    import sqlite3

    conn = sqlite3.connect(str(configured_db))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    expected = {
        "incidents", "attempts", "environments", "projects", "technologies", "tags",
        "attachments", "extracted_texts", "commands", "revisions",
        "incident_technologies", "incident_projects", "incident_tags",
        "incident_relations", "technology_relations",
        "extraction_provenance", "chunk_embeddings",
        "incidents_fts", "incidents_fts_trigram",
        "alembic_version",
    }
    assert expected <= tables


def test_downgrade_then_upgrade_round_trips(configured_db: Path):
    alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")

    import sqlite3

    conn = sqlite3.connect(str(configured_db))
    try:
        assert conn.execute("SELECT count(*) FROM incidents").fetchone()[0] == 0
    finally:
        conn.close()


async def test_fts5_stays_in_sync_with_incidents(db_session):
    await db_session.execute(
        text(
            "INSERT INTO incidents (id, raw_problem, raw_solution, title, status, "
            "needs_ai_review) VALUES (1, :problem, 'Installed CUDA-enabled build', "
            "'Jetson CUDA failure', 'solved', 0)"
        ),
        {"problem": "CUDA unavailable after reinstall, error ECONNREFUSED seen in logs"},
    )
    await db_session.commit()

    hits = (
        await db_session.execute(
            text("SELECT rowid FROM incidents_fts WHERE incidents_fts MATCH 'CUDA'")
        )
    ).fetchall()
    assert [r[0] for r in hits] == [1]

    trigram_hits = (
        await db_session.execute(
            text(
                "SELECT rowid FROM incidents_fts_trigram "
                "WHERE incidents_fts_trigram MATCH 'ECONNREFUSED'"
            )
        )
    ).fetchall()
    assert [r[0] for r in trigram_hits] == [1]

    await db_session.execute(text("DELETE FROM incidents WHERE id=1"))
    await db_session.commit()

    remaining = (
        await db_session.execute(text("SELECT count(*) FROM incidents_fts"))
    ).scalar_one()
    assert remaining == 0
