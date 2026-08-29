from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply-us-m1-schema.ps1"


def test_us_schema_runtime_guard_uses_current_repository_app_tree() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'docker compose run --rm --no-deps -T' in text
    assert '--volume "${repoRoot}\\app:/app/app:ro"' in text
    assert 'from app.us.migrations import ensure_us_m1_schema' in text
    assert 'Running US schema runtime guard against current repository code' in text


def test_us_schema_apply_remains_us_scoped_and_does_not_manage_service_lifecycle() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "004_us_m1_core.sql" in text
    assert "005_us_m11_real_tdxf.sql" in text
    assert "006_us_m12_snapshot_semantics.sql" in text
    assert "007_us_m13_official_fact_families.sql" in text
    assert "008_us_m14_change_history.sql" in text
    assert "002_us_status_reference.sql" in text
    assert "003_us_semantic_reference.sql" in text
    assert "004_us_event_roles.sql" in text

    forbidden = (
        "docker compose up",
        "docker compose down",
        "docker compose restart",
        "docker restart",
        "docker stop",
        "replay-us-deterministic.ps1",
        "run-us-capacity-pilot.ps1",
        "2023_5.zip",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_us_schema_apply_does_not_reference_cn_schema_files() -> None:
    lowered = SCRIPT.read_text(encoding="utf-8").lower()

    assert "database/clickhouse/init/0" in lowered
    assert "database/postgres/init/0" in lowered
    assert "cn_" not in lowered
