"""Recurring GB journal governance tests; no production mutation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.global_trademarks import gb_journal_weekly as weekly


class FakeCursor:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def execute(self, _sql: str) -> None:
        pass

    def fetchall(self) -> list[dict]:
        return self.rows


class GBJournalWeeklyTests(unittest.TestCase):
    def test_operator_uses_existing_exclusive_json_writer(self) -> None:
        self.assertTrue(callable(weekly.e_stage.write_json_exclusive))

    def test_issue_set_binds_complete_ordered_prefix(self) -> None:
        rows = [
            {
                "issue": "2026-039",
                "source_zip_sha256": "a" * 64,
                "stage_records_sha256": "b" * 64,
                "status": "COMPLETE",
                "checkpoint_notice_ordinal": 3,
                "expected_notices": 3,
            },
            {
                "issue": "2026-040",
                "source_zip_sha256": "c" * 64,
                "stage_records_sha256": "d" * 64,
                "status": "COMPLETE",
                "checkpoint_notice_ordinal": 4,
                "expected_notices": 4,
            },
        ]
        evidence = weekly.issue_set_evidence(FakeCursor(rows))
        self.assertEqual(evidence["issue_count"], 2)
        self.assertEqual(evidence["latest_issue"], "2026-040")
        self.assertEqual(evidence["issues"], ["2026-039", "2026-040"])

    def test_incomplete_issue_fails_closed(self) -> None:
        rows = [
            {
                "issue": "2026-040",
                "source_zip_sha256": "a" * 64,
                "stage_records_sha256": "b" * 64,
                "status": "RUNNING",
                "checkpoint_notice_ordinal": 0,
                "expected_notices": 4,
            }
        ]
        with self.assertRaisesRegex(RuntimeError, "not completely accepted"):
            weekly.issue_set_evidence(FakeCursor(rows))

    def test_authority_token_is_issue_and_sha_bound(self) -> None:
        plan = {
            "kind": weekly.PLAN_KIND,
            "status": "FROZEN_NO_APPLY",
            "issue": {"issue": "2026-040"},
            "prestate": {"latest_issue": "2026-039", "issues": ["2026-039"]},
            "accepted_storage_topology_evidence": {},
            "postgres_target_evidence": {},
            "target_database": "markorbit",
            "target_database_physical_drive": "E",
            "future_query_storage_placement": "hot_global",
            "original_visual_authority_drive": "F",
            "operator_sha256": weekly.domestic.canonical_text_sha(weekly.Path(weekly.__file__)),
            "schema_sql_sha256": weekly.hashlib.sha256(
                weekly.pilot.SCHEMA_SQL.encode()
            ).hexdigest(),
            "issue_atomic_transaction": True,
            "archive_after_commit": True,
            "journal_observation_only": True,
            "current_state_verified": False,
            "serving_cutover_authorized": False,
            "clickhouse_apply_authorized": False,
            "source_delete_authorized": False,
        }
        with patch.object(weekly.target, "validate", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "exact weekly journal authority"):
                weekly.authorize(plan, "a" * 64, "broad approval")
            weekly.authorize(
                plan,
                "a" * 64,
                "GO #875 GB-JOURNAL-WEEKLY " + "a" * 64 + " ISSUE-2026-040-ATOMIC-ARCHIVE",
            )


if __name__ == "__main__":
    unittest.main()
