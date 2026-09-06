from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ENGMEM_", env_file=".env", extra="ignore")

    data_dir: Path = REPO_ROOT / "data"
    database_path: Path | None = None
    attachments_dir: Path | None = None

    ai_provider: str = "heuristic"  # heuristic | claude | openai_compatible
    anthropic_api_key: str | None = None
    openai_api_base: str | None = None
    openai_api_key: str | None = None

    # Embeddings auto-detect availability (model present, loads successfully) — this
    # is an explicit user opt-out (e.g. lower-resource machines, privacy preference),
    # not a gate embeddings otherwise need to pass.
    embeddings_enabled: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    cors_origins: list[str] = ["http://localhost:5173"]

    def model_post_init(self, __context) -> None:
        if self.database_path is None:
            self.database_path = self.data_dir / "engineering.db"
        if self.attachments_dir is None:
            self.attachments_dir = self.data_dir / "attachments"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
