from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_token: str
    database_url: str = "postgresql://alfa:alfa@db:5432/alfa"

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    embed_model: str = ""
    embed_dim: int = 1536
    embed_base_url: str = ""
    embed_api_key: str = ""

    default_tz: str = "Europe/Moscow"
    platform_url: str = ""
    miniapp_url: str = ""
    utm_suffix: str = ""

    kb_min_score: float = 0.30
    mat_min_score: float = 0.20

    quiet_start: int = 21
    quiet_end: int = 9
    max_reminders_per_user: int = 8
    max_reminders_per_day: int = 3
    miniapp_origins: str = "*"


settings = Settings()
