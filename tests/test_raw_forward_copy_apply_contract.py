from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-raw-forward-copy-to-f.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_operator_requires_exact_main_admin_and_explicit_apply() -> None:
    text = source()
    assert "[switch]$Apply" in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "requires elevated Administrator PowerShell" in text
    assert "Raw forward-copy must run from local main" in text
    assert "RAW_FORWARD_COPY_READY_FOR_APPLY" in text
    assert "RAW_FORWARD_COPY_PARITY_GO" in text
    assert "RAW_FORWARD_COPY_BLOCKED" in text


def test_operator_pins_d_source_f_target_and_production_invariants() -> None:
    text = source()
    for marker in (
        "F:\\MarkOrbitData\\raw",
        "SOURCE_RAW_NOT_ON_D",
        "TARGET_RAW_NOT_ON_F",
        "TARGET_CONTAINS_FOREIGN_FILES",
        "TARGET_RAW_PATH_NOT_DIRECTORY",
        "WORKER_CONTAINER_COUNT_NOT_ZERO",
        "PRODUCTION_CLICKHOUSE_NOT_HEALTHY",
        "F_HEADROOM_BELOW_RESERVE_AFTER_COPY",
        "Get-Volume -DriveLetter F",
        "512GB",
        "* 0.20",
    ):
        assert marker in text


def test_operator_uses_forward_only_robocopy_without_delete_semantics() -> None:
    text = source()
    assert "& robocopy.exe @robocopyArgs" in text
    for marker in (
        "'/E'",
        "'/COPY:DAT'",
        "'/DCOPY:DAT'",
        "'/R:1'",
        "'/W:3'",
        "'/J'",
        "'/XJ'",
    ):
        assert marker in text
    for forbidden in (
        "'/MIR'",
        "'/PURGE'",
        "'/MOVE'",
        "'/MOV'",
        "Remove-Item",
        "Move-Item",
        "Clear-Content",
        "docker volume rm",
        "docker system prune",
        "wsl --shutdown",
        "wsl.exe --shutdown",
        "2023_5.zip",
    ):
        assert forbidden not in text


def test_operator_requires_full_metadata_and_sha256_parity() -> None:
    text = source()
    for marker in (
        "Compare-MetadataExact",
        "source_manifest_stable=",
        "metadata_parity_exact=",
        "Get-FileHash -LiteralPath $sourceItem.full_path -Algorithm SHA256",
        "Get-FileHash -LiteralPath $targetItem.full_path -Algorithm SHA256",
        "sha256_progress=",
        "verified_file_count=",
        "verified_source_bytes=",
        "hash_mismatch_count=",
        "SHA256_PARITY_FAILED",
        "RAW_DATA_PATH_CUTOVER_PREFLIGHT",
    ):
        assert marker in text


def test_operator_proves_env_and_source_remain_protected() -> None:
    text = source()
    for marker in (
        "$envHashBefore",
        "$envHashAfter",
        "ENV_CHANGED_DURING_COPY",
        "source_delete_authorized=$false",
        "env_change_authorized=$false",
        "raw_move_authorized=$false",
        "vhdx_mutation_performed=$false",
        "wsl_mutation_performed=$false",
        "docker_restart_performed=$false",
        "clickhouse_mutation_performed=$false",
        "corpus_replay_performed=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ):
        assert marker in text
