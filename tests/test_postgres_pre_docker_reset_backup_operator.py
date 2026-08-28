from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "scripts" / "backup-postgres-before-docker-data-reset.ps1"


def test_postgres_pre_reset_backup_operator_is_fail_closed() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "markorbit-data-engine_postgres_data" in text
    assert "PostgreSQL must be running and healthy before backup" in text
    assert "API container is running" in text
    assert "Persistent worker is running" in text
    assert "pg_dumpall" in text
    assert "LOGICAL_BACKUP_OK" in text
    assert "docker stop --timeout 60 $PostgresContainer" in text
    assert "$postgresStoppedByOperator = $false" in text
    assert "$postgresStoppedByOperator = $true" in text
    assert "if ($postgresStoppedByOperator)" in text
    assert "type=volume,source=$PostgresVolume,target=/source,readonly" in text
    assert "tar -czf" in text
    assert "gzip -t" in text
    assert "tar -tzf" in text
    assert "Get-FileHash -Algorithm SHA256" in text
    assert "POSTGRES_RESTORED_HEALTHY" in text
    assert "POSTGRES_DUAL_BACKUP_OK" in text

    forbidden = (
        "docker desktop stop",
        "docker desktop restart",
        "wsl --shutdown",
        "diskpart",
        "optimize-vhd",
        "docker volume rm",
        "docker system prune",
        "docker compose down",
        "remove-item",
        "docker_data.vhdx",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_postgres_pre_reset_backup_keeps_recovery_on_f_drive() -> None:
    text = OPERATOR.read_text(encoding="utf-8")

    assert '"F:\\MarkOrbitData\\recovery\\postgres-before-docker-reset"' in text
    assert "manifest.json" in text
    assert "POSTGRES_BEFORE_DOCKER_DATA_RESET_V1" in text
    assert "logical_backup" in text
    assert "cold_pgdata_backup" in text
