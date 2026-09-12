from pathlib import Path


def test_projection_filters_to_stable_applicant_entities_and_tombstones_inactive_rows():
    sql = (
        Path(__file__).parents[1]
        / "database"
        / "clickhouse"
        / "init"
        / "016_cn_applicant_name_lookup_projection.sql"
    ).read_text(encoding="utf-8")
    assert "role IN ('OWNER', 'CO_OWNER')" in sql
    assert "entity_id IS NOT NULL" in sql
    assert "is_deleted = 1 OR is_current = 0" in sql
