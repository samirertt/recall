from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import attachments, health, incidents, search
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Engineering Memory", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(incidents.router)
    app.include_router(attachments.router)
    app.include_router(search.router)

    return app


app = create_app()
