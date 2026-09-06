"""extracted text search index

Revision ID: d21505cad1e4
Revises: 3fdb0d26f4d9
Create Date: 2026-09-06 16:33:08.937613

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd21505cad1e4'
down_revision: Union[str, Sequence[str], None] = '3fdb0d26f4d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Section 17: extracted attachment content becomes searchable, without ever
    altering the original evidence file. External-content FTS5 over `extracted_texts`,
    same tuned-tokenchars tokenizer as `incidents_fts` (docs/RESEARCH.md § SQLite & FTS5)."""
    op.execute(
        """
        CREATE VIRTUAL TABLE extracted_texts_fts USING fts5(
            text,
            content='extracted_texts', content_rowid='id',
            tokenize = "unicode61 remove_diacritics 2 tokenchars '_.-:#/'"
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER extracted_texts_ai AFTER INSERT ON extracted_texts BEGIN
          INSERT INTO extracted_texts_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER extracted_texts_ad AFTER DELETE ON extracted_texts BEGIN
          INSERT INTO extracted_texts_fts(extracted_texts_fts, rowid, text)
          VALUES('delete', old.id, old.text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER extracted_texts_au AFTER UPDATE ON extracted_texts BEGIN
          INSERT INTO extracted_texts_fts(extracted_texts_fts, rowid, text)
          VALUES('delete', old.id, old.text);
          INSERT INTO extracted_texts_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS extracted_texts_au")
    op.execute("DROP TRIGGER IF EXISTS extracted_texts_ad")
    op.execute("DROP TRIGGER IF EXISTS extracted_texts_ai")
    op.execute("DROP TABLE IF EXISTS extracted_texts_fts")
