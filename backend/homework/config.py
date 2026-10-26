from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class HomeworkConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOMEWORK_", env_file=".env", extra="ignore"
    )

    STORAGE_PATH: Path = Path("homework-storage")


homework_settings = HomeworkConfig()


def get_homework_settings() -> HomeworkConfig:
    return homework_settings
