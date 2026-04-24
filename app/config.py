from pathlib import Path

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT_DIR / ".env"


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    database_url: str = "sqlite:///./seo_ranker.db"

    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("openai_api_key", "openai_base_url", "openai_model", "database_url", mode="before")
    @classmethod
    def _strip_wrapped_strings(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            cleaned = cleaned[1:-1].strip()
        return cleaned

    @property
    def openai_key_masked(self) -> str:
        key = self.openai_api_key or ""
        if not key:
            return "(empty)"
        if len(key) <= 10:
            return key[:2] + "***"
        return f"{key[:6]}...{key[-4:]}"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # In local dev we want .env to be the source of truth.
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)


def get_settings() -> Settings:
    # Fresh read helps when .env is edited during long-running dev server session.
    return Settings()

settings = get_settings()
