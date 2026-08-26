from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_external_storage_override_is_explicit_and_targets_database_paths() -> None:
    override = (ROOT / "docker-compose.external-storage.yml").read_text(encoding="utf-8")

    assert "POSTGRES_DATA_PATH" in override
    assert "CLICKHOUSE_DATA_PATH" in override
    assert "CLICKHOUSE_LOG_PATH" in override
    assert ":/var/lib/postgresql/data" in override
    assert ":/var/lib/clickhouse" in override
    assert ":/var/log/clickhouse-server" in override
    assert "Set POSTGRES_DATA_PATH before enabling external storage" in override
    assert "Set CLICKHOUSE_DATA_PATH before enabling external storage" in override


def test_default_compose_keeps_existing_managed_volume_contract() -> None:
    default = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    # The external-storage foundation must be opt-in. A normal pull on the live
    # host must continue to resolve the accepted managed volumes until an explicit
    # migration/cutover is approved.
    assert "postgres_data:/var/lib/postgresql/data" in default
    assert "clickhouse_data:/var/lib/clickhouse" in default
    assert "clickhouse_logs:/var/log/clickhouse-server" in default
    assert "POSTGRES_DATA_PATH" not in default
    assert "CLICKHOUSE_DATA_PATH" not in default


def test_external_storage_preflight_is_config_only() -> None:
    script = (ROOT / "scripts" / "check-external-storage-config.ps1").read_text(
        encoding="utf-8"
    )

    assert "config --format json" in script
    for mutating_command in (
        "docker compose up",
        "docker compose down",
        "docker compose start",
        "docker compose stop",
        "docker compose restart",
        "docker compose rm",
    ):
        assert mutating_command not in script
