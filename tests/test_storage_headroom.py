from app.storage_headroom import GIB, evaluate_disk_headroom


def test_headroom_passes_when_absolute_percent_and_reserve_fit() -> None:
    report = evaluate_disk_headroom(
        disks=[
            {
                "name": "default",
                "path": "/var/lib/clickhouse/",
                "free_space": 300 * GIB,
                "total_space": 1000 * GIB,
            }
        ],
        minimum_free_gib=128,
        minimum_free_percent=10,
        reserve_gib=32,
    )
    assert report["status"] == "PASS"
    assert report["safe_to_mutate"] is True
    assert report["policy"]["required_free_bytes"] == 160 * GIB


def test_headroom_blocks_when_percentage_plus_reserve_is_stricter() -> None:
    report = evaluate_disk_headroom(
        disks=[
            {
                "name": "default",
                "path": "/var/lib/clickhouse/",
                "free_space": 220 * GIB,
                "total_space": 2000 * GIB,
            }
        ],
        minimum_free_gib=128,
        minimum_free_percent=10,
        reserve_gib=32,
    )
    assert report["status"] == "BLOCKED"
    assert report["safe_to_mutate"] is False
    assert report["policy"]["required_free_bytes"] == 232 * GIB
    assert "clickhouse_free_space_below_policy" in report["reason_codes"]


def test_headroom_prefers_default_disk() -> None:
    report = evaluate_disk_headroom(
        disks=[
            {"name": "cold", "path": "/cold", "free_space": 900 * GIB, "total_space": 1000 * GIB},
            {"name": "default", "path": "/hot", "free_space": 300 * GIB, "total_space": 1000 * GIB},
        ]
    )
    assert report["disk"]["name"] == "default"


def test_headroom_blocks_when_disk_state_missing() -> None:
    report = evaluate_disk_headroom(disks=[])
    assert report["status"] == "BLOCKED"
    assert report["safe_to_mutate"] is False
    assert report["reason_codes"] == ["clickhouse_disk_state_missing"]
