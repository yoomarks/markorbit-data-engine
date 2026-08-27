import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "scripts" / "check-clickhouse-hot-cutover-readiness.ps1"
MIGRATE = ROOT / "scripts" / "migrate-clickhouse-volume-to-hot.ps1"
COMPOSE = ROOT / "docker-compose.yml"


def _compose_service_names(text: str) -> set[str]:
    services: set[str] = set()
    in_services = False
    for line in text.splitlines():
        if line == "services:":
            in_services = True
            continue
        if not in_services:
            continue
        if line and not line.startswith(" "):
            break
        if line.startswith("  ") and not line.startswith("    "):
            stripped = line.strip()
            if stripped.endswith(":"):
                services.add(stripped[:-1])
    return services


def test_readiness_is_control_plane_and_metadata_only():
    text = READINESS.read_text(encoding="utf-8")

    assert "safe_to_cutover" in text
    assert "control.job_run" in text
    assert "status = ''RUNNING''" in text
    assert "control.source_package" in text
    assert "status = ''PROCESSING''" in text
    assert "system.parts" in text
    assert "source=$sourceVolume,target=/source,readonly" in text
    assert "find /source -type f" in text
    assert "source_regular_file_bytes" in text
    assert "cold_path_empty" in text
    assert "revalidates_source_packages = $false" in text

    forbidden = (
        "OPTIMIZE TABLE",
        "SELECT * FROM",
        "FINAL",
        "docker volume rm",
        "down -v",
        "2023_5.zip",
    )
    for marker in forbidden:
        assert marker not in text


def test_migration_is_explicit_source_preserving_and_rollback_capable():
    text = MIGRATE.read_text(encoding="utf-8")

    assert "[switch]$Execute" in text
    assert "if (-not $Execute)" in text
    assert "check-clickhouse-hot-cutover-readiness.ps1" in text
    assert '"compose", "stop", "clickhouse"' in text
    assert "source=$sourceVolume,target=/source,readonly" in text
    assert "cp -a /source/. /target/" in text
    assert "find /target -type f" in text
    assert "source_regular_file_bytes" in text
    assert "source_volume_retained = $true" in text
    assert "Start-OriginalClickHouse" in text
    assert "Assert-BaselineEqual" in text
    assert "system.disks" in text
    assert "source_packages_revalidated = $false" in text

    forbidden = (
        "docker volume rm",
        "down -v",
        "Remove-Item $sourceVolume",
        "OPTIMIZE TABLE",
        "2023_5.zip",
    )
    for marker in forbidden:
        assert marker not in text


def test_migration_closes_writer_shutdown_race_before_clickhouse_stop():
    text = MIGRATE.read_text(encoding="utf-8")

    writer_stop = text.index('if ($writersToStop.Count -gt 0)')
    second_readiness = text.index("$afterWriterStop = Invoke-Readiness")
    clickhouse_stop = text.index('Invoke-DockerText -Arguments @("compose", "stop", "clickhouse")')
    copy_data = text.index("cp -a /source/. /target/")

    assert writer_stop < second_readiness < clickhouse_stop < copy_data


def test_migration_pause_set_tracks_every_compose_application_service():
    migrate_text = MIGRATE.read_text(encoding="utf-8")
    compose_text = COMPOSE.read_text(encoding="utf-8")

    match = re.search(
        r"\$writerCandidates\s*=\s*@\((.*?)\)",
        migrate_text,
        flags=re.DOTALL,
    )
    assert match is not None
    paused_services = set(re.findall(r'"([^"]+)"', match.group(1)))

    compose_services = _compose_service_names(compose_text)
    assert {"postgres", "clickhouse"}.issubset(compose_services)
    application_services = compose_services - {"postgres", "clickhouse"}

    assert paused_services == application_services
