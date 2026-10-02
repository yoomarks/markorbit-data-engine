"""GB historical E structured-stage governance tests; synthetic files only."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections import namedtuple
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from app.global_trademarks import gb_historical_e_stage as stage

DiskUsage = namedtuple("DiskUsage", "total used free")


def frozen(relative: str, payload: bytes) -> stage.FrozenFile:
    return stage.FrozenFile(relative, len(payload), hashlib.sha256(payload).hexdigest())


class GBHistoricalEStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "d-source"
        self.target = self.root / "e-target"
        self.raw = self.root / "f-raw"
        self.gov = self.root / "gov"
        for path in (self.source, self.raw, self.gov):
            path.mkdir()
        self.stage_payloads = {
            "domestic-rows.jsonl": b"domestic\n",
            "domestic-quarantine.jsonl": b"bad-domestic\n",
            "madrid-rows.jsonl": b"madrid\n",
            "madrid-quarantine.jsonl": b"bad-madrid\n",
        }
        self.raw_payloads = {
            "opendatadomestic.zip": b"official-domestic",
            "opendataIR.zip": b"official-madrid",
        }
        self.stage_files = tuple(
            frozen(name, payload) for name, payload in self.stage_payloads.items()
        )
        self.raw_files = tuple(frozen(name, payload) for name, payload in self.raw_payloads.items())
        for name, payload in self.stage_payloads.items():
            (self.source / name).write_bytes(payload)
        for name, payload in self.raw_payloads.items():
            (self.raw / name).write_bytes(payload)
        self.audit = self.gov / "audit.json"
        self.audit.write_text(
            json.dumps(
                {
                    "status": "TWO_HISTORICAL_ZIPS_STAGED_NOT_DATABASE_INGESTED",
                    "accepted_source_rows": 1_298_573,
                    "quarantined_source_rows": 114,
                    "original_F_ZIPs_retained": True,
                    "data_engine_ingested": False,
                }
            ),
            encoding="utf-8",
        )
        self.disk = DiskUsage(total=10_000_000, used=1_000_000, free=9_000_000)

    def patches(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch.object(stage, "SOURCE_ROOT", self.source))
        stack.enter_context(patch.object(stage, "TARGET_ROOT", self.target))
        stack.enter_context(patch.object(stage, "RAW_ROOT", self.raw))
        stack.enter_context(patch.object(stage, "GOV_ROOT", self.gov))
        stack.enter_context(patch.object(stage, "ACCEPTED_AUDIT", self.audit))
        stack.enter_context(
            patch.object(stage, "ACCEPTED_AUDIT_SHA", stage.sha256_file(self.audit))
        )
        stack.enter_context(patch.object(stage, "STAGE_FILES", self.stage_files))
        stack.enter_context(patch.object(stage, "RAW_FILES", self.raw_files))
        stack.enter_context(patch.object(stage, "TARGET_MANIFEST", self.target / "manifest.json"))
        stack.enter_context(patch.object(stage, "RECEIPT", self.gov / "receipt.json"))
        stack.enter_context(patch.object(stage, "reserve_floor", return_value=100))
        stack.enter_context(patch.object(stage.shutil, "disk_usage", return_value=self.disk))
        return stack

    def test_plan_pins_e_structured_f_raw_and_no_mutation_authority(self) -> None:
        with self.patches():
            plan = stage.make_plan()
        self.assertEqual(plan["target_drive"], "E")
        self.assertEqual(plan["official_raw_authority_drive"], "F")
        self.assertEqual(plan["source_role"], "LEGACY_SUPERSEDED_D_STAGE_COPY_SOURCE_ONLY")
        self.assertTrue(plan["historical_source_only"])
        self.assertFalse(plan["current_state_verified"])
        self.assertFalse(plan["source_delete_authorized"])
        self.assertFalse(plan["postgres_apply_authorized"])
        self.assertFalse(plan["clickhouse_apply_authorized"])
        self.assertFalse(plan["vhdx_operation_authorized"])
        self.assertFalse(plan["serving_cutover_authorized"])
        self.assertNotIn("free_bytes", json.dumps(plan))

    def test_exact_authority_token_is_required(self) -> None:
        with self.patches():
            plan = stage.make_plan()
            plan_sha = "a" * 64
            token = "GO #855 GB-HISTORICAL-E-STAGE " + plan_sha + " ADDITIVE-COPY-VERIFY-NO-DELETE"
            stage.validate_plan(plan, plan_sha, token)
            with self.assertRaisesRegex(
                stage.GBHistoricalEStageError, "exact GB E structured-stage"
            ):
                stage.validate_plan(plan, plan_sha, token + " EXTRA")

    def test_apply_copies_exact_files_retains_sources_and_writes_receipt(self) -> None:
        with self.patches():
            plan = stage.make_plan()
            receipt = stage.apply_stage(plan, "b" * 64)
            self.assertEqual(receipt["files_copied_this_run"], 4)
            self.assertEqual(receipt["files_reused_this_run"], 0)
            self.assertTrue(receipt["source_D_retained"])
            self.assertTrue(receipt["official_raw_F_retained"])
            stage.verify_files(self.target, self.stage_files)
            stage.verify_files(self.source, self.stage_files)
            stage.verify_files(self.raw, self.raw_files)

    def test_copy_resumes_only_exact_complete_partial(self) -> None:
        item = self.stage_files[0]
        self.target.mkdir()
        (self.target / (item.relative + ".partial")).write_bytes(
            (self.source / item.relative).read_bytes()
        )
        with self.patches():
            copied = stage.copy_file(item)
        self.assertTrue(copied)
        self.assertTrue((self.target / item.relative).is_file())
        self.assertFalse((self.target / (item.relative + ".partial")).exists())

    def test_mismatching_target_or_partial_fails_without_overwrite(self) -> None:
        item = self.stage_files[0]
        self.target.mkdir()
        target = self.target / item.relative
        target.write_bytes(b"wrong")
        with self.patches():
            with self.assertRaisesRegex(stage.GBHistoricalEStageError, "mismatch"):
                stage.copy_file(item)
        self.assertEqual(target.read_bytes(), b"wrong")

        target.unlink()
        partial = self.target / (item.relative + ".partial")
        partial.write_bytes(b"incomplete")
        with self.patches():
            with self.assertRaisesRegex(stage.GBHistoricalEStageError, "requires review"):
                stage.copy_file(item)
        self.assertEqual(partial.read_bytes(), b"incomplete")

    def test_unexpected_target_file_fails_before_copy(self) -> None:
        self.target.mkdir()
        unexpected = self.target / "unreviewed.txt"
        unexpected.write_text("do not overwrite", encoding="utf-8")
        with self.patches():
            plan = {
                "target_total_bytes": self.disk.total,
                "target_reserve_floor_bytes": 100,
            }
            with self.assertRaisesRegex(stage.GBHistoricalEStageError, "unexpected files"):
                stage.apply_stage(plan, "c" * 64)
        self.assertEqual(unexpected.read_text(encoding="utf-8"), "do not overwrite")
        self.assertFalse((self.target / self.stage_files[0].relative).exists())

    def test_source_or_raw_drift_fails_before_target_creation(self) -> None:
        (self.raw / self.raw_files[0].relative).write_bytes(b"tampered")
        with self.patches():
            with self.assertRaisesRegex(stage.GBHistoricalEStageError, "identity drift"):
                stage.make_plan()
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
