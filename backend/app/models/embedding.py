"""Chunk embeddings (Phase 8) — derived, rebuildable via `rebuild-embeddings`.

Storage is deliberately a plain BLOB column, not a vector-index virtual table (see
docs/RESEARCH.md § Vector Index): brute-force NumPy cosine search over this table is
the v1 retrieval mechanism; sqlite-vec is the documented upgrade path if ever needed.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.incident import Incident


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )

    chunk_key: Mapped[str] = mapped_column(String(50))  # 'problem' | 'solution' | 'full' | ...
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # float32 bytes, L2-normalized
    dims: Mapped[int] = mapped_column(Integer)

    model_name: Mapped[str] = mapped_column(String(200), index=True)
    model_revision: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    incident: Mapped["Incident"] = relationship(back_populates="embeddings")
