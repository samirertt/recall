"""Health endpoint — a minimal preview of the full `engkb doctor` command (Phase 13)."""

from fastapi import APIRouter

from app.core.config import get_settings
from app.db.session import is_fts5_available
from app.services.embeddings.provider import get_embedding_provider

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    provider = get_embedding_provider()
    return {
        "status": "ok",
        "database_path": str(settings.database_path),
        "fts5_available": is_fts5_available(),
        "ai_provider": settings.ai_provider,
        "embedding_provider_available": provider is not None,
        "embedding_model": provider.model_name if provider else None,
    }
