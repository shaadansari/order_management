"""Application configuration loaded from environment variables / .env.

WHY a central Settings object: every config value is read in exactly one place and
type-checked, so there are no hardcoded secrets/URLs scattered through the codebase
and no `os.getenv(...)` calls with default drift in every file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "sqlite:///./order_management.db"

    # JWT
    jwt_secret_key: str = "change-me-in-production-please"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60

    # Redis (optional — the app degrades gracefully to the DB if Redis is unreachable)
    redis_url: str = "redis://localhost:6379/0"

    # App
    app_env: str = "development"
    api_version: str = "v1"


# Single shared instance imported across the app.
settings = Settings()
