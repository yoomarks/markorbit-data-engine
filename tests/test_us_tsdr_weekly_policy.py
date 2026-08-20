from datetime import date, datetime, timedelta, timezone

from app.us_tsdr.policy import Candidate, rank_candidate, select_weekly_batch


NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def test_new_applications_are_hard_first_lane():
    rows = [
        Candidate("99000001", 101, applicant_country="US", current_attorney_present=True, is_new_application=True),
        Candidate("88000001", 50, applicant_country="CN", current_attorney_present=False),
    ]
    selected = select_weekly_batch(rows, capacity=1, now=NOW)
    assert selected[0].candidate.serial_number == "99000001"
    assert "NEW_APPLICATION" in selected[0].reason_codes


def test_recent_new_signal_is_hard_only_until_first_success():
    rows = [
        Candidate("99000001", 100, is_new_application=True, never_fetched=False),
        Candidate("99000002", 100, is_new_application=True, never_fetched=True),
    ]
    selected = select_weekly_batch(rows, capacity=10, now=NOW)
    assert [item.candidate.serial_number for item in selected] == ["99000002"]
    assert selected[0].hard_new_application is True


def test_terminal_complete_is_permanently_retired():
    item = Candidate(
        "77000001",
        20,
        lifecycle_state="TERMINAL_INVALID",
        never_fetched=False,
        terminal_complete=True,
    )
    assert rank_candidate(item, now=NOW) is None


def test_terminal_invalid_never_fetched_is_one_shot_task():
    item = Candidate("77000002", 20, lifecycle_state="TERMINAL_INVALID", never_fetched=True)
    ranked = rank_candidate(item, now=NOW)
    assert ranked is not None
    assert ranked.task_type == "TERMINAL_INITIAL_FETCH"
    assert "TERMINAL_INVALID_ONE_SHOT" in ranked.reason_codes


def test_fresh_active_case_without_event_or_demand_is_not_rescheduled():
    item = Candidate(
        "76000001",
        20,
        lifecycle_state="REFRESHABLE",
        never_fetched=False,
        last_fetched_at=NOW - timedelta(days=3),
        refresh_due_at=NOW + timedelta(days=30),
    )
    assert rank_candidate(item, now=NOW) is None


def test_cn_unrepresented_beats_generic_historical_backfill():
    rows = [
        Candidate("75000001", 10, filing_date=date(2000, 1, 1), applicant_country="US"),
        Candidate("75000002", 11, filing_date=date(2025, 1, 1), applicant_country="CN", current_attorney_present=False),
    ]
    selected = select_weekly_batch(rows, capacity=2, now=NOW)
    assert selected[0].candidate.serial_number == "75000002"
    assert "CN_NO_CURRENT_ATTORNEY" in selected[0].reason_codes


def test_failed_refresh_is_retried_even_when_not_due():
    item = Candidate(
        "76000002",
        20,
        lifecycle_state="REFRESHABLE",
        never_fetched=False,
        retry_required=True,
        last_fetched_at=NOW - timedelta(days=10),
        refresh_due_at=NOW + timedelta(days=30),
    )
    ranked = rank_candidate(item, now=NOW)
    assert ranked is not None
    assert ranked.task_type == "REFRESH"
    assert "RETRY_PREVIOUS_FAILURE" in ranked.reason_codes
