"""Journal full-import governance tests; no production mutation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.global_trademarks import gb_journal_full as full


def summaries() -> list[dict]:
    values = []
    for index in range(full.ACCEPTED_ISSUES):
        year = 2025 if index < 39 else 2026
        number = index + 14 if year == 2025 else index - 38
        issue = f"{year}-{number:03d}"
        values.append(
            {
                "issue": issue,
                "source_zip_sha256": f"{index + 1:064x}",
                "stage_records_sha256": f"{index + 101:064x}",
                "expected_notices": index + 1,
                "expected_goods": index + 2,
                "expected_parties": index + 3,
                "expected_visuals": index + 4,
                "expected_missing_visuals": 1 if index == 0 else 0,
                "ordered_row_identity_sha256": f"{index + 201:064x}",
            }
        )
    return sorted(values, key=lambda value: value["issue"])


def topology() -> dict:
    return {
        "contract_version": full.pilot.STORAGE_TOPOLOGY_VERSION,
        "contract_sha256": "1" * 64,
        "gb_hot_placement": {"drive": "E", "placement": "hot_global"},
        "accepted_docker_e_receipt_sha256": full.pilot.ACCEPTED_DOCKER_E_RECEIPT_SHA,
        "docker_desktop_data_root": str(full.pilot.DOCKER_E_ROOT),
        "docker_data_vhdx": str(full.pilot.DOCKER_E_DATA_VHDX),
        "docker_engine_version": "29.6.2",
        "docker_root_dir": "/var/lib/docker",
    }


def postgres() -> dict:
    return {
        "database": "markorbit",
        "data_directory": full.pilot.POSTGRES_DATA_DIRECTORY,
        "server_version_num": 160015,
        "server_port": 5432,
        "server_address": "172.18.0.3",
        "observed_server_port": 5432,
        "system_identifier": "123456789",
        "configured_endpoint_host": "localhost",
        "configured_endpoint_port": 5432,
        "container_id": "c" * 64,
        "container_image_id": "sha256:" + "d" * 64,
        "compose_project": "markorbit-data-engine",
        "container_addresses": ["172.18.0.3"],
        "volume_name": "markorbit-data-engine_postgres_data",
        "volume_source": "/var/lib/docker/volumes/markorbit-data-engine_postgres_data/_data",
    }


def proof() -> dict:
    return {
        "pilot": {
            "plan_sha256": full.JOURNAL_STAGE_PLAN_SHA,
            "receipt_sha256": full.JOURNAL_STAGE_RECEIPT_SHA,
            "manifest_sha256": "2" * 64,
        }
    }


def plan() -> dict:
    with patch.object(
        full.domestic,
        "require_e_disk_reserve",
        return_value={"E": {"free_bytes": 10, "reserve_bytes": 5}},
    ):
        return full.make_plan(
            proof(),
            summaries(),
            execution_main="f" * 40,
            storage_topology=topology(),
            postgres_target=postgres(),
        )


class GBJournalFullTests(unittest.TestCase):
    def test_plan_binds_accepted_pilot_stage_and_observation_semantics(self) -> None:
        value = plan()
        self.assertEqual(value["accepted_issue_count"], 78)
        self.assertEqual(value["remaining_issue_count"], 77)
        self.assertEqual(value["pilot_issue"], "2026-033")
        self.assertEqual(value["pilot_plan_sha256"], full.PILOT_PLAN_SHA)
        self.assertEqual(value["pilot_receipt_sha256"], full.PILOT_RECEIPT_SHA)
        self.assertEqual(value["journal_e_stage_plan_sha256"], full.JOURNAL_STAGE_PLAN_SHA)
        self.assertEqual(value["journal_e_stage_receipt_sha256"], full.JOURNAL_STAGE_RECEIPT_SHA)
        self.assertEqual(value["target_database_physical_drive"], "E")
        self.assertEqual(value["original_visual_authority_drive"], "F")
        self.assertTrue(value["journal_observation_only"])
        self.assertFalse(value["current_state_verified"])
        self.assertFalse(value["serving_cutover_authorized"])
        self.assertFalse(value["source_cleanup_authorized"])

    def test_exact_full_authority_is_required(self) -> None:
        value = plan()
        plan_sha = "3" * 64
        token = "GO #855 GB-JOURNAL-FULL-ACCEPTED " + plan_sha + " REMAINING-77-ISSUE-ATOMIC"
        full.authorize(value, plan_sha, token)
        with self.assertRaisesRegex(RuntimeError, "exact GB journal full"):
            full.authorize(value, plan_sha, token + " EXTRA")
        with self.assertRaisesRegex(RuntimeError, "frozen plan/operator mismatch"):
            full.authorize({**value, "current_state_verified": True}, plan_sha, token)

    def test_committed_remaining_issues_must_be_exact_prefix(self) -> None:
        planned = ["2025-014", "2025-015", "2026-033", "2026-034"]
        self.assertEqual(
            full.validate_committed_order(["2025-014", "2025-015", "2026-033"], planned),
            ["2025-014", "2025-015"],
        )
        with self.assertRaisesRegex(RuntimeError, "exact resumable prefix"):
            full.validate_committed_order(["2025-015", "2026-033"], planned)
        with self.assertRaisesRegex(RuntimeError, "unplanned issue"):
            full.validate_committed_order(["2026-033", "2026-099"], planned)

    def test_issue_set_digest_changes_with_source_identity(self) -> None:
        accepted = summaries()
        digest = full._issue_set_sha(accepted)
        changed = [dict(value) for value in accepted]
        changed[0]["source_zip_sha256"] = "9" * 64
        self.assertNotEqual(digest, full._issue_set_sha(changed))

    def test_issue_receipt_preserves_topology_and_no_cutover(self) -> None:
        value = plan()
        issue = value["issues"][0]
        receipt = full._issue_receipt(
            value,
            "4" * 64,
            issue,
            {"E": {"free_bytes": 10, "reserve_bytes": 5}},
        )
        self.assertEqual(receipt["kind"], full.ISSUE_RECEIPT_KIND)
        self.assertEqual(receipt["target_database_physical_drive"], "E")
        self.assertEqual(receipt["original_visual_authority_drive"], "F")
        self.assertTrue(receipt["journal_observation_only"])
        self.assertFalse(receipt["current_state_verified"])
        self.assertFalse(receipt["serving_cutover_authorized"])


if __name__ == "__main__":
    unittest.main()
