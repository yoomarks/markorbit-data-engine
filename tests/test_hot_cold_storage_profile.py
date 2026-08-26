from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hot_cold_compose_override_is_explicit_and_opt_in() -> None:
    text = (ROOT / "docker-compose.hot-cold-storage.yml").read_text(encoding="utf-8")

    assert "CLICKHOUSE_HOT_DATA_PATH" in text
    assert "CLICKHOUSE_COLD_DATA_PATH" in text
    assert "CLICKHOUSE_LOG_PATH" in text
    assert ":/var/lib/clickhouse\"" in text
    assert ":/var/lib/clickhouse-cold\"" in text
    assert ":/var/log/clickhouse-server\"" in text
    assert "hot-cold-storage.xml:/etc/clickhouse-server/config.d/hot-cold-storage.xml:ro" in text
    assert "docker compose up" not in text.lower()


def test_clickhouse_hot_cold_policy_maps_default_to_hot_and_sata_to_cold() -> None:
    config_path = ROOT / "database" / "clickhouse" / "config" / "hot-cold-storage.xml"
    root = ET.parse(config_path).getroot()

    storage = root.find("storage_configuration")
    assert storage is not None
    assert storage.findtext("disks/cold/path") == "/var/lib/clickhouse-cold/"
    assert int(storage.findtext("disks/cold/keep_free_space_bytes") or "0") >= 64 * 1024**3

    hot_disks = [node.text for node in storage.findall("policies/hot_cold/volumes/hot/disk")]
    cold_disks = [node.text for node in storage.findall("policies/hot_cold/volumes/cold/disk")]
    assert hot_disks == ["default"]
    assert cold_disks == ["cold"]
    assert float(storage.findtext("policies/hot_cold/move_factor") or "0") == 0.10


def test_env_example_documents_current_windows_drive_assignment() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "CLICKHOUSE_HOT_DATA_PATH=E:/MarkOrbitData/hot/clickhouse" in text
    assert "CLICKHOUSE_COLD_DATA_PATH=F:/MarkOrbitData/cold/clickhouse" in text
    assert "CLICKHOUSE_LOG_PATH=E:/MarkOrbitData/hot/clickhouse-logs" in text


def test_preflight_is_read_only_and_fail_closed() -> None:
    text = (ROOT / "scripts" / "check-hot-cold-storage.ps1").read_text(encoding="utf-8")
    lowered = text.lower()

    assert "activation_authorized = $false" in lowered
    assert "config --quiet" in lowered
    assert "hot and cold storage must be on different drives" in lowered
    assert "clickhouse logs must remain on the hot drive" in lowered

    forbidden = (
        "docker compose up",
        "docker compose down",
        "docker compose restart",
        "docker compose create",
        "docker compose rm",
        "remove-item",
        "move-item",
        "copy-item",
        "robocopy",
        "alter table",
        "optimize table",
    )
    assert not [token for token in forbidden if token in lowered]
