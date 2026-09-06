from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.search import SearchResponse
from app.services.retrieval.search import search_incidents

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str, limit: int = 20, session: AsyncSession = Depends(get_db)
) -> SearchResponse:
    return await search_incidents(session, q, limit=limit)
