from pathlib import Path

from app.storage_consumer_inventory import (
    TABLE_CONTRACTS,
    build_inventory,
    scan_table_consumers,
)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_static_scan_classifies_serving_reads_and_runtime_writes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "app/main_core.py",
        """
def case():
    return query(\"SELECT * FROM markorbit_facts.cn_goods_item_current FINAL\")
""",
    )
    _write(
        tmp_path,
        "app/cn/native_party.py",
        """
def publish(client):
    client.command(\"INSERT INTO markorbit_facts.cn_case_party_current SELECT 1\")
""",
    )

    scanned = scan_table_consumers(tmp_path)
    goods = scanned["cn_goods_item_current"]
    party = scanned["cn_case_party_current"]

    assert goods[0]["category"] == "serving_api"
    assert goods[0]["access_mode"] == "read"
    assert party[0]["category"] == "publisher_runtime"
    assert party[0]["access_mode"] == "write"


def test_static_scan_does_not_treat_docs_as_consumers(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/design.md",
        "cn_observed_event and cn_case_party_relation_history are discussed here.",
    )
    scanned = scan_table_consumers(tmp_path)
    assert scanned["cn_observed_event"] == []
    assert scanned["cn_case_party_relation_history"] == []


def test_contract_decisions_keep_serving_current_tables_hot() -> None:
    assert TABLE_CONTRACTS["cn_goods_item_current"]["current_tier_decision"] == "HOT_REQUIRED"
    assert TABLE_CONTRACTS["cn_case_party_current"]["current_tier_decision"] == "HOT_REQUIRED"
    assert (
        TABLE_CONTRACTS["cn_observed_event"]["current_tier_decision"]
        == "HOT_WITH_COMPACTABLE_BASELINE"
    )
    assert (
        TABLE_CONTRACTS["cn_case_party_relation_history"]["current_tier_decision"]
        == "WARM_CANDIDATE_PENDING_VERIFICATION"
    )


def test_repository_has_required_serving_anchors() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    report = build_inventory(repo_root)
    by_table = {row["table"]: row for row in report["tables"]}

    assert report["status"] == "PASS"
    assert report["missing_serving_anchors"] == []
    assert by_table["cn_goods_item_current"]["direct_serving_read_count"] > 0
    assert by_table["cn_observed_event"]["direct_serving_read_count"] > 0
    assert by_table["cn_case_party_current"]["direct_serving_read_count"] > 0


def test_relation_history_is_not_promoted_to_hot_by_storage_policy_references() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    report = build_inventory(repo_root)
    relation = next(
        row for row in report["tables"] if row["table"] == "cn_case_party_relation_history"
    )

    assert relation["current_tier_decision"] == "WARM_CANDIDATE_PENDING_VERIFICATION"
    assert relation["serving_contract"] == "NO_DIRECT_CASE_API_PAYLOAD_FOUND_BY_STATIC_CONTRACT_SCAN"
