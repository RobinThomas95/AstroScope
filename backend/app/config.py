"""Application settings, loaded from environment variables and the repo-root ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Look for .env in the repo root first (../.env relative to backend/), then the CWD.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
_ENV_FILES = tuple(str(p) for p in (_REPO_ROOT_ENV, Path(".env")) if p.exists()) or (".env",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # External API keys (used by later modules; optional for Modules 1-2).
    ads_dev_key: str | None = Field(default=None, alias="ADS_DEV_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    # Server
    host: str = Field(default="0.0.0.0", alias="ASTROSCOPE_HOST")
    port: int = Field(default=8000, alias="ASTROSCOPE_PORT")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="ASTROSCOPE_CORS_ORIGINS"
    )

    # Remote services
    remote_timeout: float = Field(default=20.0, alias="ASTROSCOPE_REMOTE_TIMEOUT")
    cache_ttl: int = Field(default=3600, alias="ASTROSCOPE_CACHE_TTL")

    simbad_tap_url: str = Field(
        default="https://simbad.cds.unistra.fr/simbad/sim-tap", alias="ASTROSCOPE_SIMBAD_TAP_URL"
    )
    mast_tap_url: str | None = Field(
        default=None,
        alias="ASTROSCOPE_MAST_TAP_URL",
        description="Override the MAST TAP endpoint (default: auto-detect)",
    )
    mocserver_url: str = Field(
        default="https://alasky.cds.unistra.fr/MocServer/query", alias="ASTROSCOPE_MOCSERVER_URL"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
