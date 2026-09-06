# Backup, Portability & Migration

Reference for `scripts/engkb export`/`import` and moving this knowledge base between
machines. Design rationale lives in
[docs/RESEARCH.md § Backup, Portability & Migrations](RESEARCH.md#backup-portability--migrations);
this is the practical how-it-works and how-to-use-it guide, kept in sync with
`backend/app/services/backup/service.py`.

## Archive format

```
<name>.zip
├── manifest.json              # read first; describes and checksums everything else
├── core.db                    # SQLite snapshot, produced via VACUUM INTO
├── attachments.sha256.txt     # BagIt-style payload manifest: "<sha256>  <path>" per line
└── attachments/<sha[:2]>/<sha><ext>   # content-addressed, deduplicated
```

**Embeddings and the FTS5 index are never included.** At this project's scale,
rebuilding both after import is cheap (`engkb rebuild-index`, `engkb
rebuild-embeddings`) and categorically safer than restoring a binary index that might
not match the importing machine's SQLite build or embedding model — see
docs/RESEARCH.md's rationale on vector-index ABI mismatches.

`manifest.json` records: manifest format version, app name, creation timestamp, the
exact Alembic schema revision (plus the oldest revision this app build can still
migrate from), the source SQLite version and backup method, row counts, a sha256 for
`core.db` and for the attachments manifest itself, and per-attachment metadata
(count, total bytes). Everything needed to decide "can I trust and understand this
archive" is in this one file, read before anything else.

## Export

```bash
scripts/engkb export backup-2026-09-06.zip
```

Safe to run against a live, in-use database. Steps, in order:

1. `PRAGMA wal_checkpoint(TRUNCATE)` + `PRAGMA integrity_check` on the live DB.
2. `VACUUM INTO` a temporary snapshot file — never a raw file copy, which under WAL
   mode can yield a torn/corrupt result if a writer is mid-transaction.
3. Every attachment on disk is copied into the archive under a content-addressed path
   keyed by its already-known sha256 (recorded per-attachment since Phase 6).
4. `manifest.json` and `attachments.sha256.txt` are written, then everything is zipped.

## Import

```bash
scripts/engkb import backup-2026-09-06.zip
```

**Fail-closed**, in this exact order, entirely before anything touches the live
database or filesystem:

1. Zip's own CRC integrity (`testzip()`).
2. `manifest_version` must be one this app build understands.
3. Recompute sha256 of `core.db` and every listed attachment; any mismatch aborts.
4. `PRAGMA integrity_check` on the extracted `core.db`.
5. Schema compatibility: if the archive's Alembic revision is older than this app's
   head, the **extracted copy** (never the live database) is upgraded in place first,
   via a temporarily-repointed `ENGMEM_DATABASE_PATH` — the live DB is never touched
   until every check above has passed. If the archive's revision isn't one this app
   build recognizes at all (i.e., a newer or divergent schema), import is refused
   outright rather than attempting a best-effort partial read.

Only after all of that does the verified copy get swapped into place, and
attachments get restored to `data/attachments/` by looking up each DB row's
`sha256` in the archive's content-addressed payload. Any attachment the manifest
lists but that's actually missing from the zip is reported as a warning
(`attachments_missing`), not a hard failure — the rest of the import still completes.

**Always run these two afterward** — derived indexes are never in the archive:

```bash
scripts/engkb rebuild-index
scripts/engkb rebuild-embeddings
```

## Moving to another computer

1. On the old machine: `scripts/engkb export backup.zip`.
2. Copy `backup.zip` to the new machine by whatever means (it's a single portable
   file — no database server, no external service).
3. On the new machine: `uv sync && uv run alembic upgrade head` (creates an empty DB
   at the current schema), then `scripts/engkb import backup.zip`, then
   `rebuild-index` + `rebuild-embeddings`.

## Disaster recovery

The entire canonical state is exactly two things: `data/engineering.db` and
`data/attachments/`. A manual copy of both (before running `export`, e.g. if the
tool itself is somehow broken) is itself a complete, valid backup — `export` mainly
adds integrity guarantees (a consistent point-in-time snapshot via `VACUUM INTO`
rather than a copy that might race a writer) and portability (checksums, a documented
schema-version marker, deduplicated attachments) on top of that.

If a derived index (FTS5 or the vector index) is ever suspected corrupted, that is
**never a data-loss event** — `rebuild-index`/`rebuild-embeddings` regenerate it
entirely from canonical data. If the canonical database itself fails
`PRAGMA integrity_check`, restore from the most recent `export` archive; there is no
automated `alembic downgrade` recovery path by design (see docs/RESEARCH.md's
rationale — a downgrade on live data is a much higher-risk operation than restoring a
known-good snapshot).

## Verified end-to-end

`backend/tests/test_backup.py::test_full_export_delete_import_rebuild_search_round_trip`
is exactly the Section 77 acceptance scenario, run as an automated test: export, then
genuinely delete the live database and every attachment file, import, rebuild
embeddings, and confirm both lexical and attachment-text search still find the
original incident.
