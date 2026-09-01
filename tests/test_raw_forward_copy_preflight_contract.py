from pathlib import Path


SCRIPT = Path("scripts/preflight-raw-forward-copy-to-f.ps1")
WORKFLOW = Path(".github/workflows/raw-forward-copy-preflight-runtime.yml")


def test_preflight_is_read_only_and_fail_closed() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    required = [
        "RAW_FORWARD_COPY_PREFLIGHT_V1",
        "RAW_FORWARD_COPY_PREFLIGHT_READY",
        "RAW_FORWARD_COPY_PREFLIGHT_BLOCKED",
        "RAW_FORWARD_COPY_PREFLIGHT_DONE",
        "F:\\MarkOrbitData\\raw",
        "SOURCE_RAW_NOT_ON_D",
        "TARGET_RAW_NOT_ON_F",
        "TARGET_RAW_ROOT_NONEMPTY",
        "WORKER_CONTAINER_COUNT_NOT_ZERO",
        "PRODUCTION_CLICKHOUSE_NOT_HEALTHY",
        "F_HEADROOM_BELOW_RESERVE_AFTER_COPY",
        "FORWARD_COPY_PRESERVE_D_SOURCE",
        "BYTE_PARITY",
        "RUNTIME_BIND_CUTOVER",
        "D_SOURCE_RETENTION_REVIEW",
        "copy_authorized=$false",
        "env_change_authorized=$false",
        "raw_delete_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ]
    for marker in required:
        assert marker in text

    forbidden = [
        "Copy-Item",
        "Move-Item",
        "Remove-Item",
        "robocopy ",
        "rsync ",
        "Mount-VHD",
        "Dismount-VHD",
        "Resize-VHD",
        "New-VHD",
        "mkfs.ext4",
        "Format-Volume",
        "--shutdown",
        "--unregister",
        "docker','restart",
        "docker','prune",
        "docker','volume','rm",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "2023_5.zip",
        "-Apply -All",
    ]
    for marker in forbidden:
        assert marker not in text


def test_preflight_uses_deterministic_f_reserve_floor() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "[double]$fTotalBytes * 0.20" in text
    assert "[int64](512GB)" in text
    assert "projected_free_after_copy_bytes" in text


def test_runtime_workflow_uses_repository_concurrency_contract() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "\n  pull_request:" in text
    assert "\nconcurrency:\n" in text
    assert "group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}" in text
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text
    assert "shell: powershell" in text
