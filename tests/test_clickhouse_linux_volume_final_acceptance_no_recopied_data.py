from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "scripts" / "finalize-clickhouse-linux-volume-acceptance.ps1"


def test_finalizer_is_acceptance_only() -> None:
    t = FINALIZER.read_text(encoding="utf-8").lower()
    for forbidden in (
        "repopulate_retained_linux_volume",
        "cp -a /source/. /target/",
        "find /target -mindepth 1 -maxdepth 1 -exec rm",
        "docker volume rm",
        "docker-compose.hot-cold-storage.yml",
    ):
        assert forbidden not in t
    assert "clickhouse_linux_volume_final_acceptance_pass" in t
