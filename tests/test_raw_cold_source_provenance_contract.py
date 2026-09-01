from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "profile-raw-cold-source-provenance.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_provenance_is_exact_main_admin_and_evidence_only() -> None:
    text = source()
    for marker in (
        "[string]$ExpectedMainSha",
        "Raw/Cold provenance must run from local main.",
        "Assert-ExactMain 'entry'",
        "Assert-ExactMain 'exit'",
        "requires elevated Administrator PowerShell",
        "receipt_version='RAW_COLD_SOURCE_PROVENANCE_V1'",
        "decision=$decision",
        "RAW_COLD_SOURCE_PROVENANCE_DONE",
    ):
        assert marker in text


def test_provenance_reads_real_env_path_and_runtime_bind() -> None:
    text = source()
    for marker in (
        "RAW_DATA_PATH",
        "Get-RawDataPathEvidence",
        "Resolve-Path -LiteralPath $full",
        "Get-ApiRawMountEvidence",
        "label=com.docker.compose.project=markorbit-data-engine",
        "label=com.docker.compose.service=api",
        "docker' @('inspect'",
        'Destination "/data/raw"',
        "api_raw_mount_source=",
    ):
        assert marker in text


def test_raw_tree_profile_is_one_recursive_pass_with_family_breakdown() -> None:
    text = source()
    assert "[System.IO.Directory]::EnumerateFiles($rootFull, '*', [System.IO.SearchOption]::AllDirectories)" in text
    assert text.count("[System.IO.Directory]::EnumerateFiles") == 1
    for marker in (
        "raw_file_count=",
        "raw_total_bytes=",
        "raw_scan_elapsed_seconds=",
        "raw_family=",
        "file_count=$totalFiles",
        "total_bytes=$totalBytes",
    ):
        assert marker in text


def test_provenance_only_shallow_lists_target_drives_and_known_candidates() -> None:
    text = source()
    for marker in (
        "D_root=Get-DirectoryNames 'D:\\'",
        "E_root=Get-DirectoryNames 'E:\\'",
        "F_root=Get-DirectoryNames 'F:\\'",
        "D_markorbit=Get-DirectoryNames 'D:\\MarkOrbitData'",
        "E_markorbit=Get-DirectoryNames 'E:\\MarkOrbitData'",
        "F_markorbit=Get-DirectoryNames 'F:\\MarkOrbitData'",
        "'F:\\raw_data'",
        "'F:\\MarkOrbitData\\raw_data'",
        "'F:\\MarkOrbitData\\raw'",
        "'F:\\yoomarks\\markorbit-data-engine\\raw_data'",
    ):
        assert marker in text


def test_provenance_never_authorizes_migration_or_copy() -> None:
    text = source()
    for marker in (
        "migration_authorized=$false",
        "env_change_authorized=$false",
        "raw_copy_authorized=$false",
        "raw_move_authorized=$false",
        "raw_delete_authorized=$false",
        "vhdx_mutation_authorized=$false",
        "docker_restart_authorized=$false",
        "clickhouse_mutation_authorized=$false",
        "corpus_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ):
        assert marker in text


def test_provenance_contains_no_host_mutation_or_bulk_replay_primitives() -> None:
    text = source()
    forbidden = (
        "Copy-Item",
        "Move-Item",
        "Remove-Item",
        "robocopy",
        "rsync",
        "Mount-VHD",
        "Dismount-VHD",
        "Resize-VHD",
        "New-VHD",
        "mkfs.ext4",
        "Format-Volume",
        "@('--mount'",
        "@('--unmount'",
        "'--shutdown'",
        "--unregister",
        "docker','restart",
        "docker','prune",
        "docker','volume','rm",
        "docker','compose','stop",
        "docker','compose','down",
        "2023_5.zip",
        "-MaxPackages 2",
        "-Apply -All",
    )
    for marker in forbidden:
        assert marker not in text
