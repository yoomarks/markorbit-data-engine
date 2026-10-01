"""GB Domestic full-resume governance tests; no production mutation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.global_trademarks import gb_domestic_full_resume as full


def proof():
    return {
        "spec": {
            "zip_sha": "3b6063bed36a78e8a04f10a5383f2881f4072a1be13e706a81ab097fb56ee571",
            "member": "OpenDataDomestic.txt",
            "manifest_sha": "aa7f52cfed7ff850af4e56060bd196d05010613ec2d930cc82bb1b74c4018dbe",
            "total": 1188992,
            "accepted": 1188886,
            "bad": 106,
        },
        "manifest": {
            "accepted_jsonl_sha256": "e7fa216355bc8cc5b9d70b05adb9d562ed5fbd58583a4a220e4a14febb070a64",
            "quarantine_jsonl_sha256": "7b487b99737873c4be9f4ec759a03d9d6158a96d437f94265b327e7c54e56727",
        },
        "e_stage": {
            "root": str(full.e_stage.STAGE_ROOT),
            "manifest_sha256": full.e_stage.STAGE_MANIFEST_SHA,
            "relocation_plan_sha256": full.e_stage.RELOCATION_PLAN_SHA,
            "relocation_receipt_sha256": full.e_stage.RELOCATION_RECEIPT_SHA,
            "independent_audit_sha256": full.e_stage.INDEPENDENT_AUDIT_SHA,
        },
    }


def live():
    return {
        "source_stream": "DOMESTIC",
        "source_member": "OpenDataDomestic.txt",
        "expected_source_rows": 1188992,
        "expected_accepted_rows": 1188886,
        "expected_quarantine_rows": 106,
        "checkpoint_source_ordinal": 1000,
        "rows_committed": 1000,
        "accepted_committed": 1000,
        "quarantine_committed": 0,
        "status": "RUNNING",
        "live_total": 1000,
        "live_min_ordinal": 1,
        "live_max_ordinal": 1000,
        "live_accepted": 1000,
        "live_quarantined": 0,
        "live_unapproved_current": 0,
        "live_nonhistorical": 0,
        "live_rows_past_checkpoint": 0,
    }


class GBDomesticFullResumeTests(unittest.TestCase):
    def test_accepted_pilot_plan_operator_sha_is_exact(self):
        self.assertEqual(
            full.PILOT_OPERATOR_SHA,
            "bacf2d6caa800958d8572bfbd6861dce392a162e82ab4d0a1d4c70322b44187a",
        )
        self.assertEqual(
            full.PILOT_PLAN_SHA,
            "5e5c9a16d96d37068a130e8a5dec52fb0cb39dbe3f461638e90fdddb13ba4be2",
        )

    def test_live_checkpoint_accepts_only_exact_pilot_state(self):
        with patch.object(full, "_readonly_live_state", return_value=live()):
            state = full.verify_live_checkpoint(proof())
        self.assertEqual(state["checkpoint_source_ordinal"], 1000)

        drift = dict(live(), current_state_verified=True)
        drift["live_unapproved_current"] = 1
        with patch.object(full, "_readonly_live_state", return_value=drift):
            with self.assertRaisesRegex(RuntimeError, "checkpoint/residency"):
                full.verify_live_checkpoint(proof())

    def test_plan_is_historical_only_and_no_serving_authority(self):
        with patch.object(
            full,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 10, "reserve_bytes": 5}},
        ):
            plan = full.make_plan(proof(), live())
        self.assertEqual(plan["status"], "FROZEN_NO_APPLY")
        self.assertEqual(plan["start_checkpoint_source_ordinal"], 1000)
        self.assertEqual(plan["target_checkpoint_source_ordinal"], 1188992)
        self.assertEqual(plan["remaining_source_rows"], 1187992)
        self.assertEqual(plan["batch_size"], 5000)
        self.assertEqual(plan["structured_stage_drive"], "E")
        self.assertEqual(plan["target_database_physical_drive"], "E")
        self.assertEqual(plan["future_query_storage_placement"], "hot_global")
        self.assertTrue(plan["historical_source_only"])
        self.assertFalse(plan["current_state_verified"])
        self.assertFalse(plan["journal_ingest_authorized"])
        self.assertFalse(plan["api_cutover_authorized"])
        self.assertFalse(plan["clickhouse_cutover_authorized"])
        self.assertFalse(plan["source_cleanup_authorized"])
        self.assertEqual(
            plan["disk_reserve_gate"],
            {"E": {"reserve_bytes": 5}},
        )
        self.assertNotIn("free_bytes", str(plan["disk_reserve_gate"]))

    def test_dynamic_free_space_does_not_change_frozen_plan(self):
        with patch.object(
            full,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 20, "reserve_bytes": 5}},
        ):
            first = full.make_plan(proof(), live())
        with patch.object(
            full,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 888, "reserve_bytes": 5}},
        ):
            second = full.make_plan(proof(), live())
        self.assertEqual(first, second)

    def test_apply_reserve_accepts_free_space_drift_but_not_floor_or_low_free(self):
        gate = {"E": {"reserve_bytes": 5}}
        with patch.object(
            full,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 7, "reserve_bytes": 5}},
        ):
            current = full.verify_apply_disk_reserve(gate)
        self.assertEqual(current["E"]["free_bytes"], 7)

        with patch.object(
            full,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 7, "reserve_bytes": 6}},
        ):
            with self.assertRaisesRegex(RuntimeError, "reserve floor changed"):
                full.verify_apply_disk_reserve(gate)

        with patch.object(
            full,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 4, "reserve_bytes": 5}},
        ):
            with self.assertRaisesRegex(RuntimeError, "below frozen reserve floor"):
                full.verify_apply_disk_reserve(gate)

    def test_e_stage_authority_is_exact_and_old_v1_kind_is_rejected(self):
        with patch.object(
            full,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 10, "reserve_bytes": 5}},
        ):
            plan = full.make_plan(proof(), live())
        plan_sha = "a" * 64
        token = "GO #855 GB-DOMESTIC-FULL-RESUME " + plan_sha + " CHECKPOINT-1000-TO-1188992"
        full.authorize(plan, plan_sha, token)
        with self.assertRaisesRegex(RuntimeError, "frozen plan/operator mismatch"):
            full.authorize(
                {**plan, "kind": "GB_DOMESTIC_HISTORICAL_FULL_RESUME_PLAN_V1"},
                plan_sha,
                token,
            )
        with self.assertRaisesRegex(RuntimeError, "exact GB Domestic"):
            full.authorize(plan, plan_sha, token + " EXTRA")

    def test_batch_and_total_bound_are_fixed(self):
        self.assertEqual(full.START_CHECKPOINT, 1000)
        self.assertEqual(full.TARGET_SOURCE_ROWS, 1188992)
        self.assertEqual(full.BATCH_SIZE, 5000)
        self.assertLess(full.BATCH_SIZE, full.TARGET_SOURCE_ROWS - full.START_CHECKPOINT)


if __name__ == "__main__":
    unittest.main()
