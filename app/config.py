from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ModuleNotFoundError:
    BaseSettings = None
    SettingsConfigDict = None


def _fallback_raw_data_root() -> Path:
    value = os.environ.get("RAW_DATA_ROOT", "").strip()
    if not value:
        env_path = Path(".env")
        if env_path.is_file():
            for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw_value = line.split("=", 1)
                if key.strip() != "RAW_DATA_ROOT":
                    continue
                value = raw_value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                break
    return Path(value or "./raw_data")


if BaseSettings is not None:

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
        # Visual assets can be detached from the database/runtime SSD. Keep these
        # unset for the legacy layout; a large HDD can own raw official assets while
        # an SSD owns canonical/search derivatives.
        visual_raw_root: Path | None = None
        visual_processed_root: Path | None = None
        cn_scan_interval_seconds: int = 300
        contact_scan_interval_seconds: int = 60
        us_download_enabled: bool = False
        us_base_url: str = ""

        # QCC enrichment is deliberately opt-in. The acquisition operator is a
        # separate scheduler-friendly process so enabling it never changes the
        # behavior of the long-running CN/contact workers.
        cn_qcc_acquisition_enabled: bool = False
        cn_qcc_capacity: int = 500
        cn_qcc_refresh_days: int = 180
        cn_qcc_cycle_interval_seconds: int = 3600
        cn_qcc_stale_batch_hours: int = 168
        cn_qcc_outgoing_root: Path | None = None
        cn_qcc_incoming_root: Path | None = None

        # USPTO mark-image acquisition is a separate, opt-in worker. Defaults keep
        # one official request slot every 2.5 seconds while local image analysis and
        # persistence execute between request starts.
        us_mark_image_request_interval_seconds: float = 2.5
        us_mark_image_http_timeout_seconds: int = 30
        us_mark_image_queue_floor: int = 20_000
        us_mark_image_queue_target: int = 100_000
        us_mark_image_recent_lookback_days: int = 14
        us_mark_image_idle_sleep_seconds: int = 60

        # Consumer-facing /api/v1 authentication is disabled by default for local
        # compatibility. G1 uses required mode with environment-scoped bearer keys.
        integration_auth_mode: str = "disabled"
        integration_api_keys: str = ""

        # MO-DE-005 backpressure is opt-in so G0 changes no live runtime defaults.
        # When enabled, the default envelope is 120 requests per 60 seconds for each
        # source IP as observed by a provider process; consumers must honor 429 and
        # Retry-After rather than assuming these defaults are universal.
        integration_rate_limit_enabled: bool = False
        integration_rate_limit_max_requests: int = 120
        integration_rate_limit_window_seconds: int = 60

        log_level: str = "INFO"

        @property
        def resolved_visual_raw_root(self) -> Path:
            return self.visual_raw_root or self.raw_data_root

        @property
        def resolved_visual_processed_root(self) -> Path:
            return self.visual_processed_root or (self.raw_data_root / "visual_processed")

        @property
        def resolved_cn_qcc_outgoing_root(self) -> Path:
            return self.cn_qcc_outgoing_root or (self.raw_data_root / "outgoing" / "cn_qcc")

        @property
        def resolved_cn_qcc_incoming_root(self) -> Path:
            return self.cn_qcc_incoming_root or (self.raw_data_root / "incoming" / "cn_qcc")

        @property
        def postgres_dsn(self) -> str:
            return (
                f"postgresql://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )

else:

    class Settings:
        """Minimal stdlib-only settings view for read-only operator planning.

        Full application/runtime settings still require pydantic-settings. This
        fallback intentionally exposes only RAW_DATA_ROOT so dry-run source
        planning can run on an operator host without installing runtime packages.
        """

        def __init__(self) -> None:
            self.raw_data_root = _fallback_raw_data_root()

        def __getattr__(self, name: str):
            raise ModuleNotFoundError(
                "pydantic-settings is required for application runtime setting "
                f"{name!r}; stdlib-only fallback permits only raw_data_root"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
