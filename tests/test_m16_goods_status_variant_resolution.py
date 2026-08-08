from pathlib import Path


def test_same_package_status_variants_use_semantic_precedence_not_source_order():
    source = Path("app/cn/goods_lifecycle_sql.py").read_text(encoding="utf-8")
    assert 'INTRA_PACKAGE_STATUS_RESOLUTION_VERSION = "CN_GOODS_STATUS_RESOLUTION_V1_STRONGEST_SIGNAL"' in source
    assert "goods_status_raw = '2', toUInt8(70)" in source
    assert "goods_status_raw = '1', toUInt8(60)" in source
    assert "goods_status_raw = '0', toUInt8(50)" in source
    assert "tuple(status_precedence, toUInt64(stage_source_start_line))" in source
    assert "argMax(\n                        goods_status_raw," in source


def test_identity_audit_treats_status_variants_as_observations_not_identity_collision():
    source = Path("app/cn/audit_goods_identity.py").read_text(encoding="utf-8")
    assert '"status_variant_keys"' in source
    assert '"status_variant_excess_rows"' in source
    assert "identity_tuple_count > 1" in source
    assert '"conflicting_identity_keys"' in source
    assert '"status_variant_samples"' in source


def test_runtime_emits_status_resolution_contract_version():
    source = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    assert 'metrics["intra_package_status_resolution_version"]' in source
    assert "INTRA_PACKAGE_STATUS_RESOLUTION_VERSION" in source
