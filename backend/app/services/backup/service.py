"""Backup/export/import (Phase 14; docs/RESEARCH.md § Backup, Portability & Migrations).

Format: a zip with `manifest.json` (read first, describes everything else), a
`core.db` snapshot produced via `VACUUM INTO` (never a raw file copy under WAL —
that can yield a torn/corrupt file), and content-addressed attachments. Derived
indexes (FTS5, embeddings) are excluded by default — rebuilding is cheaper and safer
than restoring a binary index that may not match this machine's SQLite/extension ABI.

Import is fail-closed: zip integrity -> manifest version -> checksums -> schema
compatibility, all before anything touches the live database.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import get_settings

MANIFEST_VERSION = "1.0"
APP_NAME = "engineering-memory"
REPO_ROOT = Path(__file__).resolve().parents[4]


class BackupError(Exception):
    pass


@dataclass
class ImportReport:
    incidents_restored: int
    attachments_restored: int
    attachments_missing: list[str]
    schema_migrated: bool
    derived_indexes_rebuilt: list[str]
    warnings: list[str]


def _alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def _alembic_head() -> str:
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def _alembic_base() -> str:
    bases = ScriptDirectory.from_config(_alembic_config()).get_bases()
    return bases[0] if bases else ""


def _upgrade_database_file(db_path: Path) -> None:
    """Runs `alembic upgrade head` against an arbitrary SQLite file, not the live
    configured database. `alembic/env.py` always builds its engine from
    `Settings.database_url`, so this temporarily repoints `ENGMEM_DATABASE_PATH` at
    `db_path`, runs the upgrade, then restores whatever was there before — needed
    specifically for migrating an *imported* archive's core.db before it's swapped
    into place, without ever touching the live database mid-import."""
    from alembic import command

    previous = os.environ.get("ENGMEM_DATABASE_PATH")
    os.environ["ENGMEM_DATABASE_PATH"] = str(db_path)
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(), "head")
    finally:
        if previous is None:
            os.environ.pop("ENGMEM_DATABASE_PATH", None)
        else:
            os.environ["ENGMEM_DATABASE_PATH"] = previous
        get_settings.cache_clear()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    try:
        counts = {}
        for table in ("incidents", "attachments", "tags", "technologies", "projects"):
            counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return counts
    finally:
        conn.close()


def export_archive(dest_path: Path) -> dict:
    """Snapshots the live DB (via VACUUM INTO, never a raw file copy) plus all
    attachments into a single portable zip. Safe to run against a live, in-use
    database — VACUUM INTO produces a consistent point-in-time copy."""
    settings = get_settings()
    db_path = settings.database_path
    attachments_dir = settings.attachments_dir

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BackupError(f"Source database failed integrity check: {integrity}")
    finally:
        conn.close()

    work_dir = dest_path.parent / f".{dest_path.stem}.tmp"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    try:
        core_db_path = work_dir / "core.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(f"VACUUM INTO '{core_db_path}'")
        finally:
            conn.close()

        core_db_sha256 = _sha256_file(core_db_path)
        core_db_size = core_db_path.stat().st_size

        # Content-addressed attachment payload, keyed off the sha256 already
        # recorded per-attachment (Phase 6) — no need to recompute it here.
        attachments_manifest_lines = []
        attachments_out_dir = work_dir / "attachments"
        attachments_out_dir.mkdir()
        attachment_count = 0
        attachment_total_bytes = 0
        if attachments_dir.exists():
            for incident_dir in attachments_dir.iterdir():
                if not incident_dir.is_dir():
                    continue
                for file_path in incident_dir.iterdir():
                    if not file_path.is_file():
                        continue
                    sha256 = _sha256_file(file_path)
                    ext = file_path.suffix
                    shard = attachments_out_dir / sha256[:2]
                    shard.mkdir(exist_ok=True)
                    archive_rel = f"attachments/{sha256[:2]}/{sha256}{ext}"
                    shutil.copy2(file_path, shard / f"{sha256}{ext}")
                    attachments_manifest_lines.append(f"{sha256}  {archive_rel}")
                    attachment_count += 1
                    attachment_total_bytes += file_path.stat().st_size

        (work_dir / "attachments.sha256.txt").write_text(
            "\n".join(attachments_manifest_lines) + ("\n" if attachments_manifest_lines else "")
        )
        attachments_manifest_sha256 = _sha256_file(work_dir / "attachments.sha256.txt")

        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "app_name": APP_NAME,
            "created_at": datetime.now(UTC).isoformat(),
            "schema": {
                "alembic_revision": _alembic_head(),
                "min_supported_revision": _alembic_base(),
            },
            "database": {
                "path": "core.db",
                "engine": "sqlite",
                "sqlite_lib_version": sqlite3.sqlite_version,
                "backup_method": "vacuum_into",
                "sha256": core_db_sha256,
                "size_bytes": core_db_size,
                "row_counts": _row_counts(core_db_path),
            },
            "attachments": {
                "count": attachment_count,
                "total_bytes": attachment_total_bytes,
                "manifest_file": "attachments.sha256.txt",
                "hash_algorithm": "sha256",
            },
            "derived_indexes": {
                "embeddings": {"included": False, "reason": "rebuildable via rebuild-embeddings"},
                "fts5": {"included": False, "reason": "rebuildable via rebuild-index"},
            },
            "checksums": {
                "algorithm": "sha256",
                "files": {
                    "core.db": core_db_sha256,
                    "attachments.sha256.txt": attachments_manifest_sha256,
                },
            },
        }
        (work_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if dest_path.exists():
            dest_path.unlink()
        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(work_dir.rglob("*")):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(work_dir))

        return manifest
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        return json.loads(zf.read("manifest.json"))
    except KeyError as exc:
        raise BackupError("Archive has no manifest.json — refusing to import") from exc
    except json.JSONDecodeError as exc:
        raise BackupError(f"manifest.json is not valid JSON: {exc}") from exc


def import_archive(src_path: Path) -> ImportReport:
    """Fail-closed: any verification failure aborts before touching the live
    database. Never partially applies an archive that doesn't check out."""
    settings = get_settings()
    warnings: list[str] = []

    try:
        zf_context = zipfile.ZipFile(src_path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError(f"Not a valid archive: {exc}") from exc

    with zf_context as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise BackupError(f"Archive is corrupt (bad CRC on {bad_file})")

        manifest = _read_manifest(zf)
        if manifest.get("manifest_version") != MANIFEST_VERSION:
            raise BackupError(
                f"Unsupported manifest_version {manifest.get('manifest_version')!r} "
                f"(this build understands {MANIFEST_VERSION!r})"
            )

        work_dir = src_path.parent / f".{src_path.stem}.import.tmp"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True)

        try:
            zf.extractall(work_dir)

            core_db_path = work_dir / manifest["database"]["path"]
            actual_sha256 = _sha256_file(core_db_path)
            expected_sha256 = manifest["database"]["sha256"]
            if actual_sha256 != expected_sha256:
                raise BackupError(
                    f"core.db checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
                )

            attachments_manifest_path = work_dir / manifest["attachments"]["manifest_file"]
            attachments_present: dict[str, Path] = {}
            if attachments_manifest_path.exists():
                for line in attachments_manifest_path.read_text().splitlines():
                    if not line.strip():
                        continue
                    expected_hash, rel_path = line.split("  ", 1)
                    file_path = work_dir / rel_path
                    if not file_path.exists():
                        warnings.append(f"Listed attachment missing from archive: {rel_path}")
                        continue
                    actual_hash = _sha256_file(file_path)
                    if actual_hash != expected_hash:
                        raise BackupError(f"Attachment checksum mismatch: {rel_path}")
                    attachments_present[expected_hash] = file_path

            conn = sqlite3.connect(str(core_db_path))
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise BackupError(f"Imported database failed integrity check: {integrity}")
            finally:
                conn.close()

            archive_revision = manifest["schema"]["alembic_revision"]
            current_head = _alembic_head()
            schema_migrated = False
            if archive_revision != current_head:
                script = ScriptDirectory.from_config(_alembic_config())
                known_revisions = {rev.revision for rev in script.walk_revisions()}
                if archive_revision not in known_revisions:
                    raise BackupError(
                        f"Archive schema revision {archive_revision} is not recognized by "
                        f"this app build (current head: {current_head}). Import with a "
                        f"compatible app version first."
                    )
                _upgrade_database_file(core_db_path)
                schema_migrated = True

            # All checks passed — now touch the live database/filesystem.
            settings.attachments_dir.mkdir(parents=True, exist_ok=True)
            if settings.database_path.exists():
                settings.database_path.unlink()
            shutil.copy2(core_db_path, settings.database_path)

            incidents_restored = _row_counts(settings.database_path).get("incidents", 0)

            attachments_restored = 0
            attachments_missing: list[str] = []
            conn = sqlite3.connect(str(settings.database_path))
            try:
                rows = conn.execute(
                    "SELECT relative_path, sha256 FROM attachments"
                ).fetchall()
            finally:
                conn.close()
            for relative_path, sha256 in rows:
                source = attachments_present.get(sha256)
                if source is None:
                    attachments_missing.append(relative_path)
                    continue
                dest = settings.attachments_dir / relative_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
                attachments_restored += 1

            return ImportReport(
                incidents_restored=incidents_restored,
                attachments_restored=attachments_restored,
                attachments_missing=attachments_missing,
                schema_migrated=schema_migrated,
                derived_indexes_rebuilt=[],
                warnings=warnings,
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
