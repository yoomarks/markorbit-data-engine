"""GB journal E structured-stage governance tests; synthetic evidence only."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections import namedtuple
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from app.global_trademarks import gb_journal_e_stage as stage

DiskUsage = namedtuple("DiskUsage", "total used free")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> str:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return sha(path)


class GBJournalEStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "d-source"
        self.target = root / "e-target"
        self.raw = root / "f-raw"
        self.visual = root / "f-visual"
        self.gov_910 = root / "gov-910"
        self.gov_855 = root / "gov-855"
        for path in (self.source, self.raw, self.visual, self.gov_910, self.gov_855):
            path.mkdir()
        self.issues = ("2026-001", "2026-002")
        self.issue_rows: list[dict] = []
        for ordinal, issue in enumerate(self.issues, 1):
            raw = self.raw / f"{issue}.zip"
            raw.write_bytes(f"official {issue}".encode())
            raw_sha = sha(raw)
            prefix = f"{issue}-{raw_sha[:12]}"
            records = self.source / f"{prefix}-details.jsonl"
            records.write_text(
                json.dumps(
                    {
                        "issue": issue,
                        "mark_id": f"UK{ordinal}",
                        "source_zip_sha256": raw_sha,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            missing_count = 1 if ordinal == 2 else 0
            manifest_payload = {
                "kind": "UKIPO_JOURNAL_PILOT_MANIFEST_V1",
                "issue": issue,
                "zip_filename": raw.name,
                "zip_sha256": raw_sha,
                "zip_bytes": raw.stat().st_size,
                "detail_count": 1,
                "mark_image_links": missing_count,
                "records_path": str(records),
                "records_sha256": sha(records),
                "assets_staged": True,
                "canonical_raw_artifact_published": False,
                "data_engine_ingested": False,
            }
            if missing_count:
                manifest_payload.update(
                    {
                        "image_evidence_complete": False,
                        "approved_missing_image_audit_sha256": "pending",
                        "missing_original_image_links": 1,
                    }
                )
            manifest = self.source / f"{prefix}-manifest.json"
            self.issue_rows.append(
                {
                    "issue": issue,
                    "source_sha256": raw_sha,
                    "manifest": manifest,
                    "manifest_payload": manifest_payload,
                    "detail_rows": 1,
                    "image_links": missing_count,
                    "missing_image_links": missing_count,
                }
            )
        self.missing = self.gov_910 / "missing.json"
        missing_sha = write_json(
            self.missing,
            {
                "kind": "UKIPO_MISSING_IMAGE_AUDIT_V1",
                "issues": [
                    {
                        "issue": self.issues[1],
                        "zip_sha256": self.issue_rows[1]["source_sha256"],
                        "missing_original_links": 1,
                    }
                ],
            },
        )
        self.issue_rows[1]["manifest_payload"]["approved_missing_image_audit_sha256"] = missing_sha
        for row in self.issue_rows:
            row["manifest_sha256"] = write_json(row["manifest"], row["manifest_payload"])
        self.audit = self.gov_910 / "audit.json"
        audit_sha = write_json(
            self.audit,
            {
                "kind": "UKIPO_78_STAGE_INDEPENDENT_AUDIT_V1",
                "status": "ALL_78_STAGED_SIX_IMAGE_PARTIAL_NO_DATA_ENGINE_IMPORT",
                "issue_count": 2,
                "original_zip_count": 2,
                "details": 2,
                "image_links": 1,
                "missing_image_issue_count": 1,
                "missing_original_image_links": 1,
                "missing_media_audit_sha256": missing_sha,
                "verified_unique_cas_objects": 1,
                "verified_unique_cas_bytes": 9,
                "canonical_raw_artifact_published": False,
                "data_engine_ingested": False,
                "issues": [
                    {
                        "issue": row["issue"],
                        "source_sha256": row["source_sha256"],
                        "manifest_sha256": row["manifest_sha256"],
                        "detail_rows": row["detail_rows"],
                        "image_links": row["image_links"],
                        "missing_image_links": row["missing_image_links"],
                    }
                    for row in self.issue_rows
                ],
            },
        )
        self.visual_receipt = self.gov_910 / "visual.json"
        visual_sha = write_json(
            self.visual_receipt,
            {
                "kind": "UKIPO_GB_ORIGINAL_VISUAL_F_RELOCATION_RECEIPT_V1",
                "status": "F_ORIGINAL_VISUAL_RELOCATION_ACCEPTED_E_RETAINED",
                "accepted_78_issue_audit_sha256": audit_sha,
                "target_root": str(self.visual),
                "target_file_count_verified": 1,
                "target_bytes_verified": 9,
                "source_retained": True,
                "source_deleted": False,
                "source_delete_authorized": False,
                "data_engine_ingest_authorized": False,
                "serving_cutover_authorized": False,
            },
        )
        self.hashes = {
            "audit": audit_sha,
            "missing": missing_sha,
            "visual": visual_sha,
        }
        self.disk = DiskUsage(total=10_000_000, used=1_000_000, free=9_000_000)

    def patches(self) -> ExitStack:
        stack = ExitStack()
        values = {
            "SOURCE_ROOT": self.source,
            "TARGET_ROOT": self.target,
            "RAW_ROOT": self.raw,
            "VISUAL_ROOT": self.visual,
            "GOV_910": self.gov_910,
            "GOV_855": self.gov_855,
            "ACCEPTED_AUDIT": self.audit,
            "ACCEPTED_AUDIT_SHA": self.hashes["audit"],
            "MISSING_AUDIT": self.missing,
            "MISSING_AUDIT_SHA": self.hashes["missing"],
            "VISUAL_RECEIPT": self.visual_receipt,
            "VISUAL_RECEIPT_SHA": self.hashes["visual"],
            "TARGET_MANIFEST": self.target / "manifest.json",
            "RECEIPT": self.gov_855 / "receipt.json",
            "EXPECTED_ISSUES": self.issues,
            "EXPECTED_DETAILS": 2,
            "EXPECTED_IMAGE_LINKS": 1,
            "EXPECTED_PARTIAL_ISSUES": 1,
            "EXPECTED_MISSING_IMAGES": 1,
            "EXPECTED_VISUAL_OBJECTS": 1,
            "EXPECTED_VISUAL_BYTES": 9,
        }
        for name, value in values.items():
            stack.enter_context(patch.object(stage, name, value))
        stack.enter_context(patch.object(stage, "reserve_floor", return_value=100))
        stack.enter_context(patch.object(stage.shutil, "disk_usage", return_value=self.disk))
        return stack

    def test_inventory_pins_all_issue_handoffs_raw_zips_and_f_visual_receipt(self):
        with self.patches():
            stage_files, raw_files, issues = stage.accepted_inventory()
        self.assertEqual(len(stage_files), 4)
        self.assertEqual(len(raw_files), 2)
        self.assertEqual([row["issue"] for row in issues], list(self.issues))
        self.assertEqual(sum(row["missing_image_links"] for row in issues), 1)

    def test_plan_has_e_structured_and_f_authority_without_mutation_authority(self):
        with self.patches():
            plan = stage.make_plan()
        self.assertEqual(plan["target_drive"], "E")
        self.assertEqual(plan["official_raw_authority_drive"], "F")
        self.assertEqual(plan["original_visual_authority_drive"], "F")
        self.assertTrue(plan["journal_observation_only"])
        self.assertFalse(plan["current_registry_state_verified"])
        self.assertFalse(plan["source_delete_authorized"])
        self.assertFalse(plan["postgres_apply_authorized"])
        self.assertFalse(plan["vhdx_operation_authorized"])
        self.assertFalse(plan["serving_cutover_authorized"])
        self.assertNotIn("free_bytes", json.dumps(plan))

    def test_exact_authority_is_required(self):
        with self.patches():
            plan = stage.make_plan()
            digest = "a" * 64
            token = f"GO #855 GB-JOURNAL-E-STAGE {digest} ADDITIVE-COPY-VERIFY-NO-DELETE"
            stage.validate_plan(plan, digest, token)
            with self.assertRaisesRegex(stage.GBJournalEStageError, "exact GB journal"):
                stage.validate_plan(plan, digest, token + " EXTRA")

    def test_apply_copies_exact_files_and_retains_d_and_f(self):
        with self.patches():
            plan = stage.make_plan()
            receipt = stage.apply_stage(plan, "b" * 64)
            self.assertEqual(receipt["files_copied_this_run"], 4)
            self.assertEqual(receipt["files_reused_this_run"], 0)
            self.assertTrue(receipt["source_D_retained"])
            self.assertTrue(receipt["official_raw_F_retained"])
            self.assertTrue(receipt["original_visual_F_retained"])

    def test_source_or_raw_drift_fails_before_target_creation(self):
        (self.raw / f"{self.issues[0]}.zip").write_bytes(b"tampered")
        with self.patches():
            with self.assertRaisesRegex(stage.GBJournalEStageError, "identity drift"):
                stage.make_plan()
        self.assertFalse(self.target.exists())

    def test_unexpected_target_file_fails_without_overwrite(self):
        self.target.mkdir()
        unexpected = self.target / "unreviewed.txt"
        unexpected.write_text("retain", encoding="utf-8")
        with self.patches():
            stage_files, _, _ = stage.accepted_inventory()
            with self.assertRaisesRegex(stage.GBJournalEStageError, "unexpected files"):
                stage.verify_target_partial_state(stage_files)
        self.assertEqual(unexpected.read_text(encoding="utf-8"), "retain")


if __name__ == "__main__":
    unittest.main()
