"""Application settings: ``config/app.yaml`` overridden by ``GBYA_*`` environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GBYA_",
        yaml_file=REPO_ROOT / "config" / "app.yaml",
        extra="ignore",
    )

    env: Literal["dev", "test", "demo"] = "dev"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    data_dir: Path = Path("data")
    app_db_path: Path = Path("data/app.db")
    log_dir: Path = Path("logs")
    frontend_dist: Path = Path("frontend/dist")
    model_base_url: str = "http://127.0.0.1:8001/v1"
    worker_concurrency: int = 4
    worker_heartbeat_s: int = 10
    worker_stale_s: int = 60

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence: explicit init args > environment > app.yaml.
        return (init_settings, env_settings, YamlConfigSettingsSource(settings_cls))

    def resolve(self, path: Path) -> Path:
        """Resolve a configured path against the repository root."""
        return path if path.is_absolute() else REPO_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
