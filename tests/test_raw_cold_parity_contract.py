from pathlib import Path


SCRIPT = Path("scripts/profile-raw-cold-parity.ps1")
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_raw_cold_parity_is_read_only_and_fail_closed() -> None:
    required = [
        "RAW_COLD_PARITY_V1",
        "RAW_COLD_PARITY_DONE",
        "RAW_COLD_PARITY_NO_REUSABLE_EQUIVALENT",
        "RAW_COLD_PARITY_EQUIVALENT",
        "DESIGN_FORWARD_COPY_TO_F_RAW_ROOT",
        "DESIGN_NO_COPY_ENV_CUTOVER",
        "F:\\MarkOrbitData\\cold",
        "[System.IO.Directory]::EnumerateFiles",
        "[System.Security.Cryptography.SHA256]::Create()",
        "migration_authorized=$false",
        "env_change_authorized=$false",
        "raw_copy_authorized=$false",
        "raw_move_authorized=$false",
        "raw_delete_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ]
    for marker in required:
        assert marker in TEXT


def test_raw_cold_parity_has_no_mutating_storage_or_replay_commands() -> None:
    forbidden = [
        "Copy-Item",
        "Move-Item",
        "Remove-Item",
        "robocopy",
        "rsync",
        "Mount-VHD",
        "Dismount-VHD",
        "Resize-VHD",
        "New-VHD",
        "Format-Volume",
        "mkfs.ext4",
        "wsl --shutdown",
        "--unregister",
        "docker restart",
        "docker prune",
        "docker volume rm",
        "2023_5.zip",
        "-Apply -All",
    ]
    for marker in forbidden:
        assert marker not in TEXT


def test_hashing_is_conditional_and_bounded() -> None:
    assert "MaxSourceBytesToHash = 68719476736" in TEXT
    assert "HashCoverageThresholdPercent = 90.0" in TEXT
    assert "$hashEligible" in TEXT
    assert "$hashWithinBudget" in TEXT
    assert "if ($hashEligible -and $hashWithinBudget)" in TEXT
    assert "RAW_COLD_PARITY_HASH_BUDGET_BLOCKED" in TEXT


def test_cold_scan_only_indexes_source_relevant_candidates() -> None:
    assert "$sourceByRelative.ContainsKey($relative)" in TEXT
    assert "$sourceNameSizeKeys.ContainsKey($nameSizeKey)" in TEXT
    assert "$coldExact[$relative]" in TEXT
    assert "$coldNameSize[$nameSizeKey]" in TEXT


def test_exact_main_and_clean_tree_are_required() -> None:
    assert "git branch --show-current" in TEXT
    assert "git fetch origin main" in TEXT
    assert "Assert-ExactMain 'entry'" in TEXT
    assert "Assert-ExactMain 'exit'" in TEXT
    assert "git status --porcelain --untracked-files=no" in TEXT
