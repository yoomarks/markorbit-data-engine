from __future__ import annotations

import pytest

import app.cn.discovery_preliminary_publication_acceptance as acceptance


READ_BUDGET = {
    "max_rows_to_read": 250000,
    "max_bytes_to_read": 268435456,
    "read_overflow_mode": "throw",
}


def page(
    *,
    page_number: int,
    application_number: str,
    case_id: str,
    next_cursor: str | None,
    query_hash: str = "sha256:" + "a" * 64,
    snapshot_id: str = "cn-serving-epoch:coverage=2026-07-31:max-success-sequence=85:success-count=85",
):
    return {
        "candidate_type": "CN_TRADEMARK_PRELIMINARY_PUBLICATION",
        "query": {"query_hash": query_hash},
        "snapshot": {
            "snapshot_id": snapshot_id,
            "snapshot_kind": "CN_QUIESCENT_SERVING_EPOCH",
            "watermark": snapshot_id,
            "source_version": "git:test",
        },
        "results": [
            {
                "prelim_pub_date": "2026-07-10",
                "application_number": application_number,
                "case_id": case_id,
            }
        ],
        "next_cursor": next_cursor,
        "bounded_truncation": False,
        "read_budget": READ_BUDGET,
        "provenance": {
            "page_number": page_number,
            "result_count": 1,
            "emitted_count": page_number,
            "has_more": next_cursor is not None,
        },
    }


def test_live_acceptance_proves_two_page_replay_without_raw_results(monkeypatch):
    page1 = page(
        page_number=1,
        application_number="A001",
        case_id="00000000-0000-0000-0000-000000000001",
        next_cursor="cursor-1",
    )
    page2 = page(
        page_number=2,
        application_number="A002",
        case_id="00000000-0000-0000-0000-000000000002",
        next_cursor=None,
    )
    pages = iter([page1, page1, page2, page2])
    monkeypatch.setattr(acceptance, "execute_page", lambda request, client: next(pages))

    receipt = acceptance.build_live_acceptance(
        application_number_start="A000",
        application_number_end="A999",
        page_size=1,
        client=object(),
    )

    assert receipt["status"] == "PASS"
    assert receipt["read_only"] is True
    assert receipt["no_write_path"] is True
    assert receipt["application_number_start"] == "A000"
    assert receipt["application_number_end"] == "A999"
    assert receipt["page1_replay_match"] is True
    assert receipt["page2_replay_match"] is True
    assert receipt["page1"]["result_count"] == 1
    assert receipt["page2"]["result_count"] == 1
    assert receipt["page1"]["read_budget"] == READ_BUDGET
    assert "results" not in receipt["page1"]
    assert "results" not in receipt["page2"]


def test_live_acceptance_rejects_cross_page_duplicate(monkeypatch):
    page1 = page(
        page_number=1,
        application_number="A001",
        case_id="00000000-0000-0000-0000-000000000001",
        next_cursor="cursor-1",
    )
    duplicate_page2 = page(
        page_number=2,
        application_number="A001",
        case_id="00000000-0000-0000-0000-000000000001",
        next_cursor=None,
    )
    pages = iter([page1, page1, duplicate_page2, duplicate_page2])
    monkeypatch.setattr(acceptance, "execute_page", lambda request, client: next(pages))

    with pytest.raises(RuntimeError, match="duplicated"):
        acceptance.build_live_acceptance(
            application_number_start="A000",
            application_number_end="A999",
            page_size=1,
            client=object(),
        )


def test_live_acceptance_rejects_snapshot_change(monkeypatch):
    page1 = page(
        page_number=1,
        application_number="A001",
        case_id="00000000-0000-0000-0000-000000000001",
        next_cursor="cursor-1",
    )
    page2 = page(
        page_number=2,
        application_number="A002",
        case_id="00000000-0000-0000-0000-000000000002",
        next_cursor=None,
        snapshot_id="cn-serving-epoch:coverage=2026-08-01:max-success-sequence=86:success-count=86",
    )
    pages = iter([page1, page1, page2, page2])
    monkeypatch.setattr(acceptance, "execute_page", lambda request, client: next(pages))

    with pytest.raises(RuntimeError, match="crossed serving snapshots"):
        acceptance.build_live_acceptance(
            application_number_start="A000",
            application_number_end="A999",
            page_size=1,
            client=object(),
        )
