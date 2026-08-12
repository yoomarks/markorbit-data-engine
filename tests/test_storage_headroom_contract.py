from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_headroom_wrapper_checks_host_and_clickhouse() -> None:
    text = _script("assert-storage-headroom.ps1")
    assert "System.IO.DriveInfo" in text
    assert "app.storage_headroom" in text
    assert "MinimumHostFreeGiB = 128" in text
    assert "MinimumHostFreePercent = 10" in text
    assert "MinimumClickHouseFreeGiB = 128" in text
    assert "MinimumClickHouseFreePercent = 10" in text
    assert "ReserveGiB = 32" in text
    assert "safe_to_mutate" in text


def test_us_shared_apply_gate_always_runs_storage_headroom_first() -> None:
    text = _script("assert-domain-apply-gate.ps1")
    headroom_index = text.index("assert-storage-headroom.ps1")
    transition_index = text.index("switch ($TargetDomain)")
    assert headroom_index < transition_index


def test_cn_mutation_entrypoints_are_headroom_guarded() -> None:
    for name in ("replay-cn-full.ps1", "run-cn.ps1", "retry-cn.ps1"):
        text = _script(name)
        assert "assert-storage-headroom.ps1" in text, name
