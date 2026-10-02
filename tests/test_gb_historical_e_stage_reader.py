"""Fail-closed tests for the accepted E-resident UKIPO historical stage."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from app.global_trademarks import gb_historical_e_stage_reader as reader


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> str:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return file_sha(path)


class GBHistoricalEStageReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.stage = root / "stage"
        self.raw = root / "raw"
        self.gov = root / "gov"
        self.stage.mkdir()
        self.raw.mkdir()
        self.gov.mkdir()
        self.spec = dict(reader.pilot.SOURCE_SPECS["DOMESTIC"])
        stem = "domestic-" + self.spec["zip_sha"][:12]
        self.rows = self.stage / f"{stem}-rows.jsonl"
        self.bad = self.stage / f"{stem}-quarantine.jsonl"
        self.rows.write_text('{"record":"accepted"}\n', encoding="utf-8")
        self.bad.write_text('{"record":"quarantined"}\n', encoding="utf-8")
        self.raw_files = {
            "opendatadomestic.zip": b"domestic raw",
            "opendataIR.zip": b"madrid raw",
        }
        for name, content in self.raw_files.items():
            (self.raw / name).write_bytes(content)

    def tearDown(self):
        self.temp.cleanup()

    def evidence(self) -> dict[str, object]:
        plan_sha = "1" * 64
        source_audit = self.gov / "source.json"
        source_audit_sha = write_json(
            source_audit,
            {
                "status": "TWO_HISTORICAL_ZIPS_STAGED_NOT_DATABASE_INGESTED",
                "data_engine_ingested": False,
                "schema_migration_applied": False,
                "streams": [
                    {
                        "source_stream": "DOMESTIC",
                        "source_zip_sha256": self.spec["zip_sha"],
                        "source_rows": self.spec["total"],
                        "accepted_source_rows": self.spec["accepted"],
                        "quarantined_source_rows": self.spec["bad"],
                        "manifest_sha256": self.spec["manifest_sha"],
                        "rows_sha256": file_sha(self.rows),
                        "quarantine_sha256": file_sha(self.bad),
                    }
                ],
            },
        )
        stage_files = [
            {
                "relative_path": self.rows.name,
                "bytes": self.rows.stat().st_size,
                "sha256": file_sha(self.rows),
            },
            {
                "relative_path": self.bad.name,
                "bytes": self.bad.stat().st_size,
                "sha256": file_sha(self.bad),
            },
        ]
        raw_evidence = [
            {
                "relative_path": name,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for name, content in self.raw_files.items()
        ]
        manifest = self.stage / "manifest.json"
        manifest_sha = write_json(
            manifest,
            {
                "kind": "GB_UKIPO_HISTORICAL_E_STRUCTURED_STAGE_MANIFEST_V1",
                "status": "E_STRUCTURED_STAGE_BYTE_IDENTICAL_HISTORICAL_ONLY",
                "plan_sha256": plan_sha,
                "accepted_stage_audit_sha256": source_audit_sha,
                "target_root": str(self.stage),
                "official_raw_root": str(self.raw),
                "historical_source_only": True,
                "current_state_verified": False,
                "database_ingested": False,
                "serving_cutover_authorized": False,
                "stage_files": stage_files,
                "official_raw_files": raw_evidence,
            },
        )
        receipt = self.gov / "receipt.json"
        receipt_sha = write_json(
            receipt,
            {
                "status": "E_STRUCTURED_STAGE_ACCEPTED_D_AND_F_RETAINED",
                "plan_sha256": plan_sha,
                "target_manifest_sha256": manifest_sha,
                "target_root": str(self.stage),
                "source_D_retained": True,
                "official_raw_F_retained": True,
                "source_delete_authorized": False,
                "postgres_apply_authorized": False,
                "clickhouse_apply_authorized": False,
                "vhdx_operation_authorized": False,
                "serving_cutover_authorized": False,
                "historical_source_only": True,
                "current_state_verified": False,
            },
        )
        audit = self.gov / "audit.json"
        audit_sha = write_json(
            audit,
            {
                "kind": "GB_UKIPO_HISTORICAL_E_STRUCTURED_STAGE_INDEPENDENT_AUDIT_V1",
                "status": "E_STRUCTURED_STAGE_INDEPENDENTLY_ACCEPTED_D_AND_F_RETAINED",
                "plan_sha256": plan_sha,
                "accepted_stage_audit_sha256": source_audit_sha,
                "relocation_receipt_sha256": receipt_sha,
                "target_manifest_sha256": manifest_sha,
                "target_root": str(self.stage),
                "stage_bytes_verified": 2_132_564_100,
                "partials_found": 0,
                "unexpected_target_files": 0,
                "postgres_applied": False,
                "clickhouse_applied": False,
                "vhdx_operated": False,
                "serving_cutover_applied": False,
                "source_D_retained": True,
                "official_raw_F_retained": True,
                "historical_source_only": True,
                "current_state_verified": False,
                "stage_files": stage_files,
                "official_raw_files": raw_evidence,
            },
        )
        return {
            "plan_sha": plan_sha,
            "source_audit": source_audit,
            "source_audit_sha": source_audit_sha,
            "manifest": manifest,
            "manifest_sha": manifest_sha,
            "receipt": receipt,
            "receipt_sha": receipt_sha,
            "audit": audit,
            "audit_sha": audit_sha,
            "raw_expected": {
                row["relative_path"]: {"bytes": row["bytes"], "sha256": row["sha256"]}
                for row in raw_evidence
            },
        }

    def patches(self, evidence: dict[str, object]) -> ExitStack:
        stack = ExitStack()
        values = {
            "STAGE_ROOT": self.stage,
            "STAGE_MANIFEST": evidence["manifest"],
            "STAGE_MANIFEST_SHA": evidence["manifest_sha"],
            "SOURCE_AUDIT": evidence["source_audit"],
            "SOURCE_AUDIT_SHA": evidence["source_audit_sha"],
            "RELOCATION_PLAN_SHA": evidence["plan_sha"],
            "RELOCATION_RECEIPT": evidence["receipt"],
            "RELOCATION_RECEIPT_SHA": evidence["receipt_sha"],
            "INDEPENDENT_AUDIT": evidence["audit"],
            "INDEPENDENT_AUDIT_SHA": evidence["audit_sha"],
            "RAW_ROOT": self.raw,
            "RAW_FILES": evidence["raw_expected"],
        }
        for name, value in values.items():
            stack.enter_context(patch.object(reader, name, value))
        return stack

    def test_accepts_exact_e_stage_and_preserves_historical_semantics(self):
        evidence = self.evidence()
        with self.patches(evidence):
            proof = reader.verify_e_stage("DOMESTIC")
        self.assertEqual(proof["rows"], self.rows)
        self.assertEqual(proof["bad"], self.bad)
        self.assertEqual(proof["e_stage"]["root"], str(self.stage))
        self.assertEqual(proof["e_stage"]["manifest_sha256"], evidence["manifest_sha"])

    def test_fails_closed_when_e_stage_bytes_drift(self):
        evidence = self.evidence()
        self.rows.write_text("tampered\n", encoding="utf-8")
        with self.patches(evidence):
            with self.assertRaisesRegex(RuntimeError, "identity drift"):
                reader.verify_e_stage("DOMESTIC")

    def test_fails_closed_when_raw_f_evidence_drifts(self):
        evidence = self.evidence()
        (self.raw / "opendatadomestic.zip").write_bytes(b"tampered")
        with self.patches(evidence):
            with self.assertRaisesRegex(RuntimeError, "official F raw evidence drift"):
                reader.verify_e_stage("DOMESTIC")


if __name__ == "__main__":
    unittest.main()
