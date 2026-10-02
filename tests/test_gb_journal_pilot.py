"""GB journal issue-pilot governance tests; synthetic evidence only."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.global_trademarks import gb_journal_pilot as pilot


class GBJournalPilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.records = root / "details.jsonl"
        self.visual_root = root / "visuals"
        self.visual_root.mkdir()
        content = b"official visual"
        digest = hashlib.sha256(content).hexdigest()
        relative = f"sha256/{digest[:2]}/{digest[2:4]}/{digest}.jpg"
        visual = self.visual_root / relative
        visual.parent.mkdir(parents=True)
        visual.write_bytes(content)
        payloads = [
            {
                "kind": "UKIPO_JOURNAL_DETAIL_STAGE_V1",
                "issue": pilot.PILOT_ISSUE,
                "source_zip_sha256": "1" * 64,
                "source_member": f"{pilot.PILOT_ISSUE}/UK00000001.html",
                "detail_html_sha256": "2" * 64,
                "mark_family": "UK",
                "mark_id": "UK00000001",
                "journal_title_raw": "Trade Mark Journal No.2026/033",
                "regdate_raw": "1 January 2026",
                "mark_text": ["EXAMPLE"],
                "goods_by_class": [{"class": 9, "goods": ["Software."]}],
                "applicants": ["Owner"],
                "representatives": ["Representative: Agent"],
                "mark_images": [
                    {
                        "ordinal": 1,
                        "source_member": f"{pilot.PILOT_ISSUE}/images/one.jpg",
                        "declared_src": "images/one.jpg",
                        "sha256": digest,
                        "bytes": len(content),
                        "asset_relative_path": relative,
                        "source_member_resolution": "EXACT",
                        "thumbnail_present": True,
                    }
                ],
            },
            {
                "kind": "UKIPO_JOURNAL_DETAIL_STAGE_V1",
                "issue": pilot.PILOT_ISSUE,
                "source_zip_sha256": "1" * 64,
                "source_member": f"{pilot.PILOT_ISSUE}/WO00000002.html",
                "detail_html_sha256": "3" * 64,
                "mark_family": "WO",
                "mark_id": "WO00000002",
                "journal_title_raw": "Trade Mark Journal No.2026/033",
                "regdate_raw": None,
                "mark_text": [],
                "goods_by_class": [{"class": 35, "goods": ["Advertising.", "Advice."]}],
                "applicants": ["First", "Second"],
                "representatives": [],
                "mark_images": [],
            },
        ]
        self.records.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in payloads),
            encoding="utf-8",
        )
        self.proof = {
            "plan_sha256": "4" * 64,
            "receipt_sha256": "5" * 64,
            "manifest_sha256": "6" * 64,
            "issue": {
                "issue": pilot.PILOT_ISSUE,
                "source_sha256": "1" * 64,
                "records_sha256": hashlib.sha256(self.records.read_bytes()).hexdigest(),
                "detail_rows": 2,
                "image_links": 1,
                "missing_image_links": 0,
            },
            "records": self.records,
        }

    def parse(self):
        with patch.object(pilot.stage, "VISUAL_ROOT", self.visual_root):
            return pilot.issue_records(self.proof)

    def make_plan(self, counts):
        with patch.object(
            pilot.domestic,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 20, "reserve_bytes": 5}},
        ):
            return pilot.make_plan(self.proof, counts, execution_main="a" * 40)

    def test_issue_records_preserve_notice_goods_parties_and_f_visual_lineage(self) -> None:
        records, counts = self.parse()
        self.assertEqual(len(records), 2)
        self.assertEqual(counts["notices"], 2)
        self.assertEqual(counts["goods"], 3)
        self.assertEqual(counts["parties"], 4)
        self.assertEqual(counts["visuals"], 1)
        self.assertEqual(counts["missing_visuals"], 0)
        self.assertRegex(counts["ordered_row_identity_sha256"], r"^[0-9a-f]{64}$")

    def test_f_visual_drift_fails_closed(self) -> None:
        records, _ = self.parse()
        relative = records[0]["payload"]["mark_images"][0]["asset_relative_path"]
        (self.visual_root / relative).write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "F original visual"):
            self.parse()

    def test_plan_keeps_journal_observational_and_scopes_storage(self) -> None:
        _, counts = self.parse()
        plan = self.make_plan(counts)
        self.assertEqual(plan["target_database_physical_drive"], "E")
        self.assertEqual(plan["future_query_storage_placement"], "hot_global")
        self.assertEqual(plan["original_visual_authority_drive"], "F")
        self.assertTrue(plan["issue_atomic_rollback"])
        self.assertTrue(plan["journal_observation_only"])
        self.assertFalse(plan["current_state_verified"])
        self.assertFalse(plan["full_journal_ingest_authorized"])
        self.assertFalse(plan["serving_cutover_authorized"])
        self.assertEqual(plan["disk_reserve_gate"], {"E": {"reserve_bytes": 5}})

    def test_exact_authority_rejects_extra_scope(self) -> None:
        _, counts = self.parse()
        plan = self.make_plan(counts)
        plan.update(
            {
                "source_zip_sha256": pilot.PILOT_SOURCE_SHA,
                "stage_records_sha256": pilot.PILOT_RECORDS_SHA,
                "expected_notices": pilot.PILOT_NOTICES,
                "expected_goods": pilot.PILOT_GOODS,
                "expected_parties": pilot.PILOT_PARTIES,
                "expected_visuals": pilot.PILOT_VISUALS,
                "ordered_row_identity_sha256": pilot.PILOT_ORDERED_SHA,
            }
        )
        digest = "b" * 64
        token = f"GO #855 GB-JOURNAL-PILOT-2026-033 {digest} ISSUE-ONLY-ROLLBACK"
        pilot.authorize(plan, digest, token)
        with self.assertRaisesRegex(RuntimeError, "exact GB journal"):
            pilot.authorize(plan, digest, token + " EXTRA")

    def test_live_prestate_requires_all_pilot_tables_absent(self) -> None:
        empty = {"database": "markorbit", "relations": [None] * 5}
        with patch.object(pilot, "_readonly_live_prestate", return_value=empty):
            self.assertEqual(pilot.verify_live_prestate(), empty)
        with patch.object(
            pilot,
            "_readonly_live_prestate",
            return_value={"database": "markorbit", "relations": ["existing", *([None] * 4)]},
        ):
            with self.assertRaisesRegex(RuntimeError, "already exist"):
                pilot.verify_live_prestate()


if __name__ == "__main__":
    unittest.main()
