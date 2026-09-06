"""Health endpoint — a minimal preview of the full `engkb doctor` command (Phase 13)."""

from fastapi import APIRouter

from app.core.config import get_settings
from app.db.session import is_fts5_available

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "database_path": str(settings.database_path),
        "fts5_available": is_fts5_available(),
        "ai_provider": settings.ai_provider,
        "embeddings_enabled": settings.embeddings_enabled,
    }
