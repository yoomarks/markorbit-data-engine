from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "markorbit_control"
    postgres_user: str = "markorbit"
    postgres_password: str = "change-me-postgres"

    clickhouse_host: str = "localhost"
    clickhouse_http_port: int = 8123
    clickhouse_db: str = "markorbit_facts"
    clickhouse_user: str = "markorbit"
    clickhouse_password: str = "change-me-clickhouse"

    raw_data_root: Path = Path("./raw_data")
    cn_scan_interval_seconds: int = 300
    us_download_enabled: bool = False
    us_base_url: str = ""
    log_level: str = "INFO"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
