"""Accepted GB raw source archive governance tests; no production mutation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.global_trademarks import gb_source_archive as archive


class GBSourceArchiveTests(unittest.TestCase):
    def test_inspection_requires_exact_incoming_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            incoming = root / "incoming"
            archived = root / "archive"
            incoming.mkdir()
            source = incoming / "2026-040.zip"
            source.write_bytes(b"journal")
            item = {
                "filename": source.name,
                "sha256": archive.sha256_file(source),
                "source_kind": "JOURNAL_OBSERVATION",
                "source_key": "2026-040",
            }
            with self.assertRaisesRegex(RuntimeError, "within F"):
                archive.inspect_files(
                    [item], incoming_root=incoming, archive_root=archived, freeze=True
                )

    def test_authority_is_exact(self) -> None:
        plan = {
            "kind": archive.PLAN_KIND,
            "status": "FROZEN_NO_APPLY",
            "file_count": archive.LEGACY_FILE_COUNT,
            "files": [{"bytes": 1}] * archive.LEGACY_FILE_COUNT,
            "total_bytes": archive.LEGACY_FILE_COUNT,
            "database_evidence": {},
            "database_evidence_sha256": archive.canonical_json_sha({}),
            "accepted_storage_topology_evidence": {},
            "postgres_target_evidence": {},
            "raw_authority_drive": "F",
            "incoming_root": str(archive.INCOMING_ROOT),
            "archive_root": str(archive.ARCHIVE_ROOT),
            "operator_sha256": archive.domestic.canonical_text_sha(Path(archive.__file__)),
            "move_within_f_only": True,
            "overwrite_authorized": False,
            "source_delete_authorized": False,
            "serving_cutover_authorized": False,
            "clickhouse_apply_authorized": False,
            "vhdx_operation_authorized": False,
            "current_registry_assertion_authorized": False,
        }
        with self.assertRaisesRegex(RuntimeError, "plan/operator mismatch"):
            archive.authorize(plan, "a" * 64, "broad approval")


if __name__ == "__main__":
    unittest.main()
