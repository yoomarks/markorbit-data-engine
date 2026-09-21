from pathlib import Path


SCRIPT = Path("scripts/audit-global-hot-foundation-readiness.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_global_hot_readiness_freezes_expected_identity() -> None:
    text = _text()
    assert "GLOBAL_HOT_FOUNDATION_READINESS_V1" in text
    assert "$script:TargetDistro = 'MarkOrbit-ClickHouse'" in text
    assert "$script:HotGlobalDisk = 'hot_global'" in text
    assert "$script:HotGlobalPolicy = 'hot_global_only'" in text
    assert "$script:ProductionClickHouseRoot = 'E:\\MarkOrbitData\\production\\clickhouse'" in text
    assert "hot_global.vhdx" not in text
    assert "$script:WarmCnDisk = 'warm_cn'" in text
    assert "$script:HotUsDisk = 'hot_us'" in text


def test_global_hot_readiness_is_read_only_and_has_no_apply_surface() -> None:
    text = _text().lower()
    assert "[switch]$apply" not in text
    assert "read_only = $true" in text
    assert "mutation_performed = $false" in text
    for forbidden in (
        "new-vhd",
        "resize-vhd",
        "mount-vhd",
        "dismount-vhd",
        "wsl.exe --mount",
        "wsl.exe --unmount",
        "wsl.exe --shutdown",
        "docker compose up",
        "insert into",
        "create table",
        "alter table",
        "optimize table",
        "move partition",
        "drop table",
    ):
        assert forbidden not in text


def test_global_hot_readiness_requires_existing_target_runtime() -> None:
    text = _text()
    assert "Get-RunningWslNames" in text
    assert "'--list', '--running', '--quiet'" in text
    assert "is not already running; refusing to start it" in text
    assert "pgrep" in text
    assert "[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml" in text


def test_global_hot_readiness_checks_e_capacity_without_guessing_allocation() -> None:
    text = _text()
    assert "Get-DriveFact 'E'" in text
    assert "Get-ReserveState" in text
    assert "0.30" in text
    assert "0.20" in text
    assert "MEASURED_APPROVED_BYTES_ONLY" in text
    assert "guessed_equal_partitioning = $false" in text
    assert "us_application_amplification_reused = $false" in text
    assert "jurisdiction_capacity_claimed = $false" in text


def test_global_hot_readiness_has_deterministic_decisions() -> None:
    text = _text()
    for marker in (
        "HOT_GLOBAL_FOUNDATION_ACCEPTED",
        "HOT_GLOBAL_PROVISIONING_PLAN_REQUIRED",
        "HOT_GLOBAL_READINESS_BLOCKED",
        "FREEZE_MEASURED_HOT_GLOBAL_PROVISIONING_PLAN",
        "JURISDICTION_CAPACITY_AND_SCHEMA_REVIEW",
        "REVIEW_PARTIAL_HOT_GLOBAL_STATE",
        "REVIEW_HOT_GLOBAL_IDENTITY_DRIFT",
    ):
        assert marker in text


def test_global_hot_readiness_verifies_ext4_and_policy_mapping() -> None:
    text = _text()
    assert "findmnt" in text
    assert "FSTYPE,SOURCE,TARGET" in text
    assert "[string]$Ext4.fstype -eq 'ext4'" in text
    assert "policy_name IN ('hot_us_only','warm_cn_only','hot_global_only')" in text
    assert "name IN ('hot_us','warm_cn','hot_global')" in text
    assert "disk_name = 'hot_global'" in text


def test_global_hot_readiness_discovers_backing_without_freezing_filename() -> None:
    text = _text()
    assert "Get-VhdxInventory" in text
    assert "Resolve-HotGlobalBacking" in text
    assert "Get-ChildItem -LiteralPath $script:ProductionClickHouseRoot -Filter '*.vhdx'" in text
    assert "mount_token" in text
    assert "AMBIGUOUS_E_BACKING_CANDIDATES" in text
    assert "PARTIAL_OR_UNRESOLVED_HOT_GLOBAL_STATE" in text
    assert "REVIEW_HOT_GLOBAL_BACKING_IDENTITY" in text
    assert "e_vhdx_inventory = @($vhdxInventory)" in text
    assert "hot_global_backing = $backing" in text


def test_global_hot_readiness_contract_mode_never_touches_host() -> None:
    text = _text()
    assert "[switch]$ContractOnly" in text
    block = text.split("if ($ContractOnly)", 1)[1].split("Assert-ExactMain 'entry'", 1)[0]
    assert "GLOBAL_HOT_FOUNDATION_READINESS_CONTRACT_PASS" in block
    assert "Invoke-TargetRows" not in block
    assert "Get-DriveFact 'E'" not in block
    assert "Get-TargetVersion" not in block


def test_global_hot_readiness_receipt_keeps_mutation_authority_false() -> None:
    text = _text()
    for marker in (
        "vhdx_change_authorized = $false",
        "wsl_lifecycle_change_authorized = $false",
        "clickhouse_schema_or_data_change_authorized = $false",
        "docker_lifecycle_change_authorized = $false",
        "source_copy_replay_delete_authorized = $false",
    ):
        assert marker in text
