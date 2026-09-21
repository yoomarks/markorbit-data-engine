from pathlib import Path


SCRIPT = Path("scripts/audit-cn-hot-foundation-readiness.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_cn_hot_readiness_freezes_current_topology_contract() -> None:
    text = _text()
    assert "CN_HOT_FOUNDATION_READINESS_V1" in text
    assert "$script:TargetDistro = 'MarkOrbit-ClickHouse'" in text
    assert "$script:HotCnDisk = 'hot_cn'" in text
    assert "$script:HotCnPolicy = 'hot_cn_only'" in text
    assert "$script:WarmCnDisk = 'warm_cn'" in text
    assert "$script:WarmCnPolicy = 'warm_cn_only'" in text
    assert "$script:ProductionHotRoot = 'D:\\MarkOrbitData\\production\\clickhouse'" in text


def test_cn_hot_readiness_preserves_accepted_262_placement_contract() -> None:
    text = _text()
    for table in (
        "cn_case_current",
        "cn_case_scope_current",
        "cn_case_party_current",
        "cn_goods_item_current",
        "cn_goods_scope_lifecycle_current",
        "cn_observed_event",
    ):
        assert table in text
    assert "cn_goods_item_observation" in text
    assert "HOT_REQUIRED_CURRENT_SERVING" in text
    assert "WARM_AFTER_SUMMARY_EQUIVALENCE" in text
    assert "UNCLASSIFIED_RETAIN_AS_IS" in text
    assert "all_cn_bytes_must_move_claimed=$false" in text


def test_cn_hot_readiness_is_read_only_without_apply_surface() -> None:
    text = _text().lower()
    assert "[switch]$apply" not in text
    assert "read_only=$true" in text
    assert "mutation_performed=$false" in text
    for forbidden in (
        "new-vhd",
        "resize-vhd",
        "mount-vhd",
        "dismount-vhd",
        "wsl.exe --mount",
        "wsl.exe --unmount",
        "wsl.exe --shutdown",
        "docker compose up",
        "docker compose down",
        "insert into",
        "create table",
        "alter table",
        "optimize table",
        "move partition",
        "drop table",
    ):
        assert forbidden not in text


def test_cn_hot_readiness_requires_existing_source_and_target_runtimes() -> None:
    text = _text()
    assert "'compose','ps','--status','running','-q','clickhouse'" in text
    assert "Source ClickHouse is not healthy" in text
    assert "'--list','--running','--quiet'" in text
    assert "is not already running; refusing to start it" in text
    assert "[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml" in text


def test_cn_hot_readiness_records_current_api_and_worker_binding() -> None:
    text = _text()
    assert "Get-ServiceClickHouseBinding 'api'" in text
    assert "Get-ServiceClickHouseBinding 'worker'" in text
    assert "from app.config import get_settings; print(get_settings().clickhouse_host)" in text
    assert "CN_API_BINDING_UNEXPECTED" in text


def test_cn_hot_readiness_discovers_hot_cn_backing_without_fixed_filename() -> None:
    text = _text()
    assert "Get-VhdxInventory" in text
    assert "Resolve-HotCnBacking" in text
    assert "Get-ChildItem -LiteralPath $script:ProductionHotRoot -Filter '*.vhdx'" in text
    assert "hot_cn.vhdx" not in text
    assert "AMBIGUOUS_HOT_CN_IDENTITY" in text
    assert "PARTIAL_OR_UNRESOLVED_HOT_CN_STATE" in text
    assert "hot_cn_backing=$hotCnBacking" in text


def test_cn_hot_readiness_verifies_warm_cn_baseline_and_ext4() -> None:
    text = _text()
    assert "WARM_CN_BASELINE_MISSING_OR_AMBIGUOUS" in text
    assert "WARM_CN_BASELINE_DRIFTED" in text
    assert "findmnt" in text
    assert "FSTYPE,SOURCE,TARGET" in text
    assert "[string]$WarmCnExt4.fstype -ne 'ext4'" in text
    assert "[string]$HotCnExt4.fstype -ne 'ext4'" in text


def test_cn_hot_readiness_has_deterministic_decisions() -> None:
    text = _text()
    for marker in (
        "CN_HOT_FOUNDATION_ACCEPTED",
        "CN_HOT_PROVISIONING_PLAN_REQUIRED",
        "CN_HOT_PLACEMENT_REVIEW_REQUIRED",
        "FREEZE_CN_HOT_PROVISIONING_AND_MIGRATION_PLAN",
        "REVIEW_CN_HOT_DATA_PARITY_AND_SERVING_CUTOVER",
    ):
        assert marker in text


def test_cn_hot_readiness_contract_mode_is_host_independent() -> None:
    text = _text()
    assert "[switch]$ContractOnly" in text
    block = text.split("if ($ContractOnly)", 1)[1].split("Assert-ExactMain 'entry'", 1)[0]
    assert "CN_HOT_FOUNDATION_READINESS_CONTRACT_PASS" in block
    assert "Get-SourceHealth" not in block
    assert "Get-TargetVersion" not in block
    assert "Get-DriveFact 'D'" not in block


def test_cn_hot_readiness_receipt_keeps_all_mutation_authority_false() -> None:
    text = _text()
    for marker in (
        "cn_replay_or_rebuild_authorized=$false",
        "data_copy_authorized=$false",
        "vhdx_mutation_authorized=$false",
        "wsl_lifecycle_change_authorized=$false",
        "docker_lifecycle_change_authorized=$false",
        "clickhouse_mutation_authorized=$false",
        "api_cutover_authorized=$false",
    ):
        assert marker in text
