from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "database" / "clickhouse" / "init" / "000_schema_version.sql"
APPLY = ROOT / "scripts" / "apply-us-m1-schema.ps1"


def test_schema_version_bootstrap_is_metadata_only() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8").lower()
    assert "create database if not exists markorbit_facts" in text
    assert "create table if not exists markorbit_facts.schema_version" in text
    assert "replacingmergetree(applied_at)" in text
    assert "order by component" in text
    assert "cn_" not in text
    assert "us_" not in text
    assert "insert into" not in text


def test_us_schema_apply_bootstraps_version_table_before_us_migrations() -> None:
    text = APPLY.read_text(encoding="utf-8")
    bootstrap = text.index("000_schema_version.sql")
    core = text.index("004_us_m1_core.sql")
    owner_read = text.index("013_us_owner_read_index.sql")
    assert bootstrap < core < owner_read
