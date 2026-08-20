from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.cn_qcc.policy import QccCandidate, has_company_name_signal, score_candidate, select_candidates
from app.cn_qcc.source_candidates import _bounded_backfill_rows


def _candidate(**overrides) -> QccCandidate:
    values = {
        "entity_id": "11111111-1111-1111-1111-111111111111",
        "applicant_name": "北京示例科技有限公司",
        "normalized_name": "北京示例科技有限公司",
        "applicant_address": "北京市朝阳区示例路1号",
        "country_code": "CN",
        "region_code": "BJ",
        "city": "北京",
        "trademark_count": 12,
        "latest_application_number": "79990001",
        "source_rank": 100,
        "source_fingerprint": "a" * 64,
        "lane_reason": "HISTORICAL_BACKFILL",
        "last_result_status": "NEVER_FETCHED",
        "last_source_fingerprint": "",
        "refresh_due_at": None,
    }
    values.update(overrides)
    return QccCandidate(**values)


def test_company_name_signal_is_conservative() -> None:
    assert has_company_name_signal("北京示例科技有限公司")
    assert has_company_name_signal("Example Technology Co., Ltd.")
    assert not has_company_name_signal("张三")
    assert not has_company_name_signal("Example Brand")


def test_never_fetched_company_precedes_refresh() -> None:
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    never = _candidate()
    refresh = _candidate(
        entity_id="22222222-2222-2222-2222-222222222222",
        last_result_status="SUCCESS",
        last_source_fingerprint="a" * 64,
        refresh_due_at=now - timedelta(days=1),
    )
    selected = select_candidates([refresh, never], capacity=2, now=now)
    assert selected[0].candidate.entity_id == never.entity_id
    assert "NEVER_FETCHED" in selected[0].reason_codes
    assert "REFRESH_DUE" in selected[1].reason_codes


def test_source_identity_change_is_high_priority() -> None:
    item = _candidate(
        last_result_status="SUCCESS",
        last_source_fingerprint="b" * 64,
        lane_reason="RECENT_SOURCE_CHANGE",
    )
    planned = score_candidate(item)
    assert planned is not None
    assert "SOURCE_IDENTITY_CHANGED" in planned.reason_codes
    assert planned.task_type == "REFRESH"


def test_historical_lane_does_not_force_early_refresh() -> None:
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    item = _candidate(
        last_result_status="SUCCESS",
        last_source_fingerprint="a" * 64,
        refresh_due_at=now + timedelta(days=90),
        lane_reason="HISTORICAL_BACKFILL",
        trademark_count=999,
    )
    assert score_candidate(item, now=now) is None


def test_backfill_page_keeps_cursor_until_bucket_is_exhausted() -> None:
    rows = [
        {"entity_id": "00000000-0000-0000-0000-000000000001"},
        {"entity_id": "00000000-0000-0000-0000-000000000002"},
        {"entity_id": "00000000-0000-0000-0000-000000000003"},
    ]
    selected, watermark, exhausted = _bounded_backfill_rows(
        rows,
        scan_limit=2,
        current_watermark="",
    )
    assert [row["entity_id"] for row in selected] == [
        row["entity_id"] for row in rows[:2]
    ]
    assert watermark == rows[1]["entity_id"]
    assert exhausted is False

    tail, tail_watermark, tail_exhausted = _bounded_backfill_rows(
        rows[2:],
        scan_limit=2,
        current_watermark=watermark,
    )
    assert [row["entity_id"] for row in tail] == [
        row["entity_id"] for row in rows[2:]
    ]
    assert tail_watermark == rows[2]["entity_id"]
    assert tail_exhausted is True


def test_non_company_and_foreign_company_are_not_qcc_tasks() -> None:
    person = _candidate(applicant_name="张三")
    foreign = _candidate(applicant_name="Example Limited", country_code="GB")
    assert score_candidate(person) is None
    assert score_candidate(foreign) is None
