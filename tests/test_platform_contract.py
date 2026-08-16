from app.platform_contract import platform_contract


def test_platformization_contract_keeps_cn_runtime_compatibility() -> None:
    contract = platform_contract()

    assert contract["version"] == "MARKORBIT_PLATFORMIZATION_M1.7"
    assert contract["goal"] == "GLOBAL_SOURCE_FACT_PLATFORM_BEFORE_NEXT_JURISDICTION"
    assert contract["work_engine"]["version"] == "MARKORBIT_WORK_ENGINE_V1"
    assert contract["domain_adapter"]["version"] == "MARKORBIT_DOMAIN_ADAPTER_V1"
    assert contract["compatibility"]["cn_inflight_checkpoint_schema_preserved"] is True
    assert contract["compatibility"]["cn_source_rank_semantics_unchanged"] is True
    assert contract["compatibility"]["storage_v2_semantics_unchanged"] is True
