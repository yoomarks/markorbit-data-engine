from pathlib import Path


SOURCE = Path("app/cn/ingest.py").read_text(encoding="utf-8")


def test_stage_to_aggregate_boundary_uses_private_lineage_names():
    assert "source_start_line AS stage_source_start_line" in SOURCE
    assert "source_end_line AS stage_source_end_line" in SOURCE
    assert "min(toUInt64(stage_source_start_line)) AS source_first_line" in SOURCE
    assert "max(toUInt64(stage_source_end_line)) AS source_last_line" in SOURCE


def test_aggregate_aliases_are_not_reused_as_aggregate_inputs():
    forbidden = [
        "min(toUInt64(source_first_line)) AS source_first_line",
        "max(toUInt64(source_last_line)) AS source_last_line",
        "min(source_first_line) AS source_first_line",
        "max(source_last_line) AS source_last_line",
    ]
    for fragment in forbidden:
        assert fragment not in SOURCE


def test_party_touch_projection_has_collision_free_aliases():
    assert "def _party_touched_sql" in SOURCE
    assert "AS touched_source_file" in SOURCE
    assert "AS touched_first_line" in SOURCE
    assert "AS touched_last_line" in SOURCE
    assert "AS touched_source_row_hash" in SOURCE
    assert SOURCE.count("INNER JOIN ({party_touched}) AS touched") == 3


def test_direct_stage_current_builders_use_stage_native_lineage_fields():
    agent = SOURCE[SOURCE.index("INSERT INTO markorbit_facts.cn_agent_current") :
                   SOURCE.index("INSERT INTO markorbit_facts.cn_priority_current")]
    priority = SOURCE[SOURCE.index("INSERT INTO markorbit_facts.cn_priority_current") :
                      SOURCE.index("INSERT INTO markorbit_facts.cn_madrid_current")]
    madrid = SOURCE[SOURCE.index("INSERT INTO markorbit_facts.cn_madrid_current") :
                    SOURCE.index("# G-prefixed Madrid-designation cases")]

    for block in (agent, priority, madrid):
        assert "source_first_line" not in block
        assert "source_last_line" not in block
        assert "source_start_line" in block
        assert "source_end_line" in block


def test_fast_runtime_preflight_exists():
    validator = Path("app/cn/validate_contract.py").read_text(encoding="utf-8")
    script = Path("scripts/validate-cn-contract.ps1").read_text(encoding="utf-8")
    assert "_assert_empty_publish_compiles" in validator
    assert "_publish(" in validator
    assert "python -m app.cn.validate_contract" in script


def test_stage_schema_keeps_raw_lineage_contract():
    schema = Path("database/clickhouse/init/002_m1_schema.sql").read_text(encoding="utf-8")
    stage_tables = [
        "cn_stage_basic", "cn_stage_applicant", "cn_stage_goods",
        "cn_stage_agent", "cn_stage_priority", "cn_stage_madrid", "cn_stage_coowner",
    ]
    for index, table in enumerate(stage_tables):
        marker = f"CREATE TABLE IF NOT EXISTS markorbit_facts.{table}"
        start = schema.index(marker)
        next_positions = [schema.find("CREATE TABLE IF NOT EXISTS", start + len(marker))]
        end = next_positions[0] if next_positions[0] != -1 else len(schema)
        block = schema[start:end]
        assert "source_start_line UInt64" in block
        assert "source_end_line UInt64" in block
        assert "source_first_line UInt64" not in block
        assert "source_last_line UInt64" not in block


def test_real_retry_is_guarded_by_fast_preflight():
    script = Path("scripts/retry-cn.ps1").read_text(encoding="utf-8")
    assert "python -m app.cn.validate_contract" in script
    assert script.index("python -m app.cn.validate_contract") < script.index("/api/jobs/cn/retry")
