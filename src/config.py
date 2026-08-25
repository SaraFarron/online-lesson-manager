from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "local"
    PROJECT_NAME: str = "FastAPI Template"
    API_V1_STR: str = "/api/v1"

    # SQLite for local dev; set to postgresql+psycopg://... in production
    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"


settings = Settings()
