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

    # Large CN base packages can drive ClickHouse aggregation and joins above
    # Docker's available memory. GROUP BY / SORT spill controls are safe global
    # defaults. JOIN spilling and long HTTP waits are opt-in because ClickHouse
    # 24.8 does not support grace_hash for every strictness/storage combination
    # used by other domains, and normal interactive queries should fail promptly.
    clickhouse_max_threads: int = 4
    clickhouse_external_group_by_bytes: int = 536_870_912
    clickhouse_external_sort_bytes: int = 536_870_912
    clickhouse_join_algorithm: str = ""
    clickhouse_grace_hash_join_initial_buckets: int = 32
    clickhouse_send_receive_timeout: int = 300

    raw_data_root: Path = Path("./raw_data")
    cn_scan_interval_seconds: int = 300
    contact_scan_interval_seconds: int = 60
    us_download_enabled: bool = False
    us_base_url: str = ""

    # Consumer-facing /api/v1 authentication is disabled by default for local
    # compatibility. Production deployments can switch to required and provide
    # comma-separated bearer keys to support overlap during key rotation.
    integration_auth_mode: str = "disabled"
    integration_api_keys: str = ""

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
