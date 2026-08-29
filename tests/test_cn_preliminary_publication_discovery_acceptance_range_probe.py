from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "find-cn-preliminary-publication-discovery-acceptance-range.ps1"
HELPER = ROOT / "scripts" / "find_cn_preliminary_publication_discovery_acceptance_range.py"


def test_range_probe_uses_source_package_not_fact_table_for_range_discovery() -> None:
    text = PROBE.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "[string]$PackageName = '2023_5.zip'" in text
    assert "[int]$MaxSourceCandidates = 1024" in text
    assert "[int]$MaxValidationWindows = 64" in text
    assert "find_cn_preliminary_publication_discovery_acceptance_range.py" in text
    assert "docker compose run --rm --no-deps -T" in text
    assert '"${RepoRoot}:/workspace:ro"' in text
    assert "-w /workspace `" in text
    assert "-e PYTHONPATH=/workspace `" in text
    assert "worker `" in text
    assert "FROM markorbit_facts.cn_case_current" not in text
    assert "$SeedQuery" not in text
    assert "LIMIT $SeedLimit" not in text

    forbidden = (
        "insert into",
        "alter table",
        "truncate table",
        "drop table",
        "optimize final",
        "docker compose up",
        "docker compose restart",
        "docker compose stop",
        "docker compose down",
        "docker restart",
        "docker stop",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_source_range_helper_reuses_production_cn_parser() -> None:
    text = HELPER.read_text(encoding="utf-8")

    assert "from app.cn.reader import iter_member_rows" in text
    assert "from app.cn.zipio import iter_package_members" in text
    assert 'DEFAULT_PACKAGE_NAME = "2023_5.zip"' in text
    assert 'DEFAULT_RAW_ROOT = Path("/data/raw")' in text
    assert 'member.schema.role != "basic"' in text
    assert 'parsed.record.get("application_number"' in text
    assert 'parsed.record.get("prelim_pub_date"' in text
    assert '"range_source": "RAW_SOURCE_PACKAGE"' in text
    assert '"fact_table_range_discovery": False' in text


def test_helper_has_only_one_fact_query_and_it_is_explicitly_bounded() -> None:
    text = HELPER.read_text(encoding="utf-8")

    assert text.count("FROM markorbit_facts.cn_case_current") == 1
    query_start = text.index('sql = f"""')
    query_end = text.index('"""', query_start + len('sql = f"""'))
    query = text[query_start:query_end]

    assert "FROM markorbit_facts.cn_case_current FINAL" in query
    assert "application_number >= {_sql_string(start)}" in query
    assert "application_number < {_sql_string(end)}" in query
    assert "is_deleted = 0" in query
    assert "prelim_pub_date IS NOT NULL" in query
    assert "ORDER BY application_number ASC, toString(case_id) ASC" in query
    assert "LIMIT 3" in query
    assert "max_rows_to_read={MAX_ROWS_TO_READ}" in query
    assert "max_bytes_to_read={MAX_BYTES_TO_READ}" in query
    assert "read_overflow_mode='{READ_OVERFLOW_MODE}'" in query


def test_helper_preserves_discovery_read_ceiling_and_fail_closed_behavior() -> None:
    text = HELPER.read_text(encoding="utf-8")

    assert "MAX_ROWS_TO_READ = 250_000" in text
    assert "MAX_BYTES_TO_READ = 268_435_456" in text
    assert 'READ_OVERFLOW_MODE = "throw"' in text
    assert '"TOO_MANY_ROWS"' in text
    assert '"TOO_MANY_BYTES"' in text
    assert "continue" in text
    assert "No unbounded fact-table discovery was attempted." in text
    assert "CN_PRELIM_DISCOVERY_ACCEPTANCE_RANGE_PROBE_PASS" in text
