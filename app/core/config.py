from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    app_name: str = "Malinka"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 30
    database_url: str = f"sqlite:///{BASE_DIR / 'salon.db'}"
    auth_cookie_name: str = "salon_access_token"
    auth_cookie_secure: bool = False
    seed_demo_data: bool = False
    bootstrap_director_login: str = "Admin"
    bootstrap_director_password: str = "change-this-password"
    bootstrap_director_full_name: str = "Administrator"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        if value == "change-me-in-production":
            raise ValueError("SECRET_KEY must be overridden and cannot use the default placeholder value.")
        return value

    @field_validator("bootstrap_director_password")
    @classmethod
    def validate_bootstrap_director_password(cls, value: str) -> str:
        weak_values = {
            "change-this-password",
            "director123",
            "admin123",
            "master123",
        }
        if value in weak_values or len(value) < 8:
            raise ValueError("BOOTSTRAP_DIRECTOR_PASSWORD must be changed before first run.")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
