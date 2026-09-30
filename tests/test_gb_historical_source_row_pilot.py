"""GB historical PostgreSQL source-row pilot tests; no live database access."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.global_trademarks import gb_historical_source_row_pilot as pilot


def sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


class GBHistoricalSourceRowPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.gov = self.root / "gov"
        self.gov.mkdir()

        self.rows = self.stage / "domestic-dddddddddddd-rows.jsonl"
        accepted = [
            {
                "kind": "UKIPO_GB_STOCK_SOURCE_ROW_V2",
                "source_stream": "DOMESTIC",
                "source_archive_sha256": "d" * 64,
                "source_member": "OpenDataDomestic.txt",
                "source_row_ordinal": 1,
                "source_cells_sha256": "1" * 64,
                "application_number": "UK0000000052A",
                "applicant_name_raw": "Owner A",
                "source_status_raw": "Registered",
                "nice_classes": [1, 9],
                "historical_source_only": True,
                "current_state_verified": False,
                "source_fields": {
                    "Trade Mark": "UK0000000052A",
                    "Name": "Owner A",
                    "Status": "Registered",
                },
            },
            {
                "kind": "UKIPO_GB_STOCK_SOURCE_ROW_V2",
                "source_stream": "DOMESTIC",
                "source_archive_sha256": "d" * 64,
                "source_member": "OpenDataDomestic.txt",
                "source_row_ordinal": 3,
                "source_cells_sha256": "3" * 64,
                "application_number": "UK002198182AA",
                "applicant_name_raw": "Owner AA",
                "source_status_raw": "Registered",
                "nice_classes": [25],
                "historical_source_only": True,
                "current_state_verified": False,
                "source_fields": {
                    "Trade Mark": "UK002198182AA",
                    "Name": "Owner AA",
                    "Status": "Registered",
                },
            },
        ]
        self.rows.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in accepted),
            encoding="utf-8",
        )
        self.bad = self.stage / "domestic-dddddddddddd-quarantine.jsonl"
        quarantined = {
            "kind": "UKIPO_GB_STOCK_QUARANTINE_V2",
            "source_stream": "DOMESTIC",
            "source_archive_sha256": "d" * 64,
            "source_member": "OpenDataDomestic.txt",
            "source_row_ordinal": 2,
            "reason": "FIELD_COUNT",
            "cells": ["UK00000000999", "broken"],
        }
        self.bad.write_text(json.dumps(quarantined) + "\n", encoding="utf-8")

        self.manifest = self.stage / "domestic-dddddddddddd-manifest.json"
        manifest = {
            "source_stream": "DOMESTIC",
            "source_archive_sha256": "d" * 64,
            "source_member": "OpenDataDomestic.txt",
            "row_count": 3,
            "accepted_source_rows": 2,
            "quarantined_source_rows": 1,
            "historical_source_only": True,
            "current_state_verified": False,
            "data_engine_ingested": False,
            "accepted_jsonl_sha256": sha(self.rows),
            "quarantine_jsonl_sha256": sha(self.bad),
            "rows_path": str(self.rows),
            "quarantine_path": str(self.bad),
        }
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.spec = {
            "zip_sha": "d" * 64,
            "member": "OpenDataDomestic.txt",
            "total": 3,
            "accepted": 2,
            "bad": 1,
            "manifest_sha": sha(self.manifest),
        }
        self.audit = self.gov / "audit.json"
        audit = {
            "status": "TWO_HISTORICAL_ZIPS_STAGED_NOT_DATABASE_INGESTED",
            "data_engine_ingested": False,
            "schema_migration_applied": False,
            "streams": [
                {
                    "source_stream": "DOMESTIC",
                    "source_zip_sha256": "d" * 64,
                    "source_rows": 3,
                    "accepted_source_rows": 2,
                    "quarantined_source_rows": 1,
                    "manifest_sha256": sha(self.manifest),
                    "rows_sha256": sha(self.rows),
                    "quarantine_sha256": sha(self.bad),
                }
            ],
        }
        self.audit.write_text(json.dumps(audit), encoding="utf-8")

    def patched(self):
        return (
            patch.object(pilot, "AUDIT", self.audit),
            patch.object(pilot, "AUDIT_SHA", sha(self.audit)),
            patch.object(pilot, "STAGE", self.stage),
            patch.object(pilot, "GOV", self.gov),
            patch.object(pilot, "SOURCE_SPECS", {"DOMESTIC": self.spec}),
        )

    def test_stage_proof_and_ordered_merge_preserve_quarantine_gap(self):
        p1, p2, p3, p4, p5 = self.patched()
        with p1, p2, p3, p4, p5:
            proof = pilot.verify_stage("DOMESTIC")
            rows = list(pilot.ordered_source_records(proof, "DOMESTIC"))
        self.assertEqual([row[0] for row in rows], [1, 2, 3])
        self.assertEqual([row[1] for row in rows], ["ACCEPTED", "QUARANTINED", "ACCEPTED"])
        self.assertEqual(rows[0][2], "UK0000000052A")
        self.assertIsNone(rows[1][2])
        self.assertEqual(rows[2][2], "UK002198182AA")

    def test_plan_is_historical_only_and_has_no_cutover_authority(self):
        p1, p2, p3, p4, p5 = self.patched()
        with p1, p2, p3, p4, p5:
            proof = pilot.verify_stage("DOMESTIC")
            plan = pilot.make_plan("DOMESTIC", proof)
        self.assertEqual(plan["status"], "FROZEN_NO_APPLY")
        self.assertTrue(plan["historical_only"])
        self.assertFalse(plan["api_cutover_authorized"])
        self.assertFalse(plan["merge_authorized"])
        self.assertFalse(plan["source_cleanup_authorized"])
        self.assertEqual(plan["pilot_source_rows"], 1000)
        self.assertEqual(plan["target_database"], "markorbit")

    def test_exact_authority_token_is_required(self):
        p1, p2, p3, p4, p5 = self.patched()
        with p1, p2, p3, p4, p5:
            proof = pilot.verify_stage("DOMESTIC")
            plan = pilot.make_plan("DOMESTIC", proof)
            plan_path = self.gov / "plan.json"
            plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
            plan_sha = sha(plan_path)
            good = f"GO #855 GB-SOURCE-ROW-PILOT {plan_sha} DOMESTIC FIRST-1000-ONLY"
            pilot.authorize(plan, plan_sha, good, "DOMESTIC")
            with self.assertRaisesRegex(RuntimeError, "exact GB stock"):
                pilot.authorize(plan, plan_sha, good + " EXTRA", "DOMESTIC")

    def test_payload_keeps_historical_currentness_false(self):
        p1, p2, p3, p4, p5 = self.patched()
        with p1, p2, p3, p4, p5:
            proof = pilot.verify_stage("DOMESTIC")
            rows = list(pilot.ordered_source_records(proof, "DOMESTIC"))
            accepted = pilot.pg_params(rows[0], "DOMESTIC", proof)
        self.assertEqual(accepted[4], "ACCEPTED")
        self.assertEqual(accepted[5], "UK0000000052A")
        self.assertEqual(accepted[8], [1, 9])
        payload = json.loads(accepted[11])
        self.assertFalse(payload["current_state_verified"])
        self.assertTrue(payload["historical_source_only"])

    def test_schema_is_additive_and_not_application_number_keyed(self):
        self.assertIn("historical_source_ingest_run_v2", pilot.SCHEMA_SQL)
        self.assertIn("historical_source_row_v2", pilot.SCHEMA_SQL)
        self.assertIn(
            "PRIMARY KEY (source_archive_sha256,source_row_ordinal)",
            pilot.SCHEMA_SQL,
        )
        self.assertIn("CHECK (NOT current_state_verified)", pilot.SCHEMA_SQL)
        self.assertNotIn("PRIMARY KEY (application_number)", pilot.SCHEMA_SQL)


if __name__ == "__main__":
    unittest.main()
