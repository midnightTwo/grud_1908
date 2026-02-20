from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/securemail.db"
    SECRET_KEY: str = "change-me-in-production"
    ADMIN_PASSWORD: str = "himarra228"
    APP_NAME: str = "SecureMail"
    APP_DOMAIN: str = "mail.yourdomain.com"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    MAIL_CACHE_TTL: int = 120  # seconds
    DB_PATH: str = "./data/securemail.db"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
