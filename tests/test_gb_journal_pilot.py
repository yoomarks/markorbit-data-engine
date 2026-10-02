"""GB journal issue-pilot governance tests; synthetic evidence only."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from app.global_trademarks import gb_journal_pilot as pilot


class GBJournalPilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.root = root
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
        self.storage_topology = {
            "contract_version": pilot.STORAGE_TOPOLOGY_VERSION,
            "contract_sha256": pilot._canonical_json_sha(pilot.build_storage_topology()),
            "gb_hot_placement": {"drive": "E", "placement": "hot_global"},
            "accepted_docker_e_receipt_sha256": pilot.ACCEPTED_DOCKER_E_RECEIPT_SHA,
            "docker_desktop_data_root": str(pilot.DOCKER_E_ROOT),
            "docker_data_vhdx": str(pilot.DOCKER_E_DATA_VHDX),
            "docker_engine_version": "29.6.2",
            "docker_root_dir": "/var/lib/docker",
        }
        self.postgres_target = {
            "database": "markorbit",
            "data_directory": pilot.POSTGRES_DATA_DIRECTORY,
            "server_version_num": 160015,
            "server_port": 5432,
            "server_address": "172.18.0.3",
            "observed_server_port": 5432,
            "system_identifier": "1234567890123456789",
            "configured_endpoint_host": "localhost",
            "configured_endpoint_port": 5432,
            "container_id": "a" * 64,
            "container_image_id": "sha256:" + "b" * 64,
            "compose_project": "markorbit-data-engine",
            "container_addresses": ["172.18.0.3"],
            "volume_name": "markorbit-data-engine_postgres_data",
            "volume_source": ("/var/lib/docker/volumes/markorbit-data-engine_postgres_data/_data"),
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
            return pilot.make_plan(
                self.proof,
                counts,
                execution_main="a" * 40,
                storage_topology=self.storage_topology,
                postgres_target=self.postgres_target,
            )

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

    def test_f_visual_path_must_remain_inside_governed_root(self) -> None:
        rows = [json.loads(line) for line in self.records.read_text(encoding="utf-8").splitlines()]
        digest = rows[0]["mark_images"][0]["sha256"]
        outside = self.root / "outside" / f"{digest}.jpg"
        outside.parent.mkdir()
        outside.write_bytes(b"official visual")
        rows[0]["mark_images"][0]["asset_relative_path"] = f"../outside/{digest}.jpg"
        self.records.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        self.proof["issue"]["records_sha256"] = hashlib.sha256(
            self.records.read_bytes()
        ).hexdigest()

        with self.assertRaisesRegex(RuntimeError, "visual"):
            self.parse()

    def test_plan_keeps_journal_observational_and_scopes_storage(self) -> None:
        _, counts = self.parse()
        plan = self.make_plan(counts)
        self.assertEqual(plan["target_database_physical_drive"], "E")
        self.assertEqual(plan["future_query_storage_placement"], "hot_global")
        self.assertEqual(plan["accepted_storage_topology_evidence"], self.storage_topology)
        self.assertEqual(plan["postgres_target_evidence"], self.postgres_target)
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

    def test_live_target_must_match_frozen_cluster_endpoint_and_placement(self) -> None:
        _, counts = self.parse()
        plan = self.make_plan(counts)
        pilot.require_plan_target_matches_live(plan, self.storage_topology, self.postgres_target)

        for field, value in (
            ("system_identifier", "987654321"),
            ("configured_endpoint_port", 15432),
        ):
            drifted = dict(self.postgres_target)
            drifted[field] = value
            with self.assertRaisesRegex(RuntimeError, "cluster/endpoint/E-topology"):
                pilot.require_plan_target_matches_live(plan, self.storage_topology, drifted)

        wrong_placement = dict(self.storage_topology)
        wrong_placement["gb_hot_placement"] = {"drive": "D", "placement": "hot_cn"}
        with self.assertRaisesRegex(RuntimeError, "cluster/endpoint/E-topology"):
            pilot.require_plan_target_matches_live(plan, wrong_placement, self.postgres_target)

    def test_atomic_receipt_recovers_after_replace_failure_and_partial_file(self) -> None:
        receipt = self.root / "receipt.json"
        payload = {"kind": "RECEIPT", "status": "PASS", "rows": 2}
        expected = pilot._canonical_json_bytes(payload)

        with patch.object(pilot.os, "replace", side_effect=OSError("fault after commit")):
            with self.assertRaisesRegex(OSError, "fault after commit"):
                pilot._atomic_publish_receipt(receipt, payload)
        self.assertFalse(receipt.exists())

        digest = pilot._atomic_publish_receipt(receipt, payload)
        self.assertEqual(receipt.read_bytes(), expected)
        self.assertEqual(digest, hashlib.sha256(expected).hexdigest())
        self.assertEqual(pilot._atomic_publish_receipt(receipt, payload), digest)

        receipt.write_bytes(expected[:17])
        self.assertEqual(pilot._atomic_publish_receipt(receipt, payload), digest)
        self.assertEqual(receipt.read_bytes(), expected)

    def test_atomic_receipt_refuses_nonpartial_conflict(self) -> None:
        receipt = self.root / "receipt.json"
        receipt.write_text('{"different":true}\n', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "differs from exact committed evidence"):
            pilot._atomic_publish_receipt(receipt, {"kind": "EXPECTED"})

    def test_atomic_receipt_recovers_after_fsync_failure(self) -> None:
        receipt = self.root / "receipt.json"
        payload = {"kind": "RECEIPT", "status": "PASS"}
        with patch.object(pilot.os, "fsync", side_effect=OSError("fsync fault")):
            with self.assertRaisesRegex(OSError, "fsync fault"):
                pilot._atomic_publish_receipt(receipt, payload)
        self.assertFalse(receipt.exists())
        pilot._atomic_publish_receipt(receipt, payload)
        self.assertEqual(receipt.read_bytes(), pilot._canonical_json_bytes(payload))

    def test_reconciliation_requires_exact_committed_plan_rows_and_receipt(self) -> None:
        _, counts = self.parse()
        plan = self.make_plan(counts)
        plan_sha = "c" * 64
        payload = pilot._receipt_payload(plan, plan_sha, {"E": {"free_bytes": 20}})
        committed = {
            "source_zip_sha256": plan["source_zip_sha256"],
            "stage_records_sha256": plan["stage_records_sha256"],
            "expected_notices": plan["expected_notices"],
            "expected_goods": plan["expected_goods"],
            "expected_parties": plan["expected_parties"],
            "expected_visuals": plan["expected_visuals"],
            "expected_missing_visuals": plan["expected_missing_visuals"],
            "plan_sha256": plan_sha,
            "execution_main_sha": plan["execution_main_sha"],
            "plan_evidence": plan,
            "storage_topology_evidence": self.storage_topology,
            "postgres_target_evidence": self.postgres_target,
            "receipt_evidence": payload,
            "receipt_sha256": pilot._canonical_json_sha(payload),
            "status": "COMPLETE",
            "checkpoint_notice_ordinal": plan["expected_notices"],
        }
        live_counts = {
            "notices": plan["expected_notices"],
            "goods": plan["expected_goods"],
            "parties": plan["expected_parties"],
            "visuals": plan["expected_visuals"],
            "missing_visuals": plan["expected_missing_visuals"],
            "unapproved_current": 0,
            "nonobservation": 0,
        }
        records, _ = self.parse()
        ordered_rows = [
            {"notice_ordinal": row["ordinal"], "source_row_sha256": row["row_sha256"]}
            for row in records
        ]

        class ReconcileCursor:
            current_one = None
            current_all = None

            def execute(self, sql, params=None):
                if "FROM trademark_gb.journal_issue_ingest_run_v1" in sql:
                    self.current_one = committed
                elif "AS notices" in sql:
                    self.current_one = live_counts
                elif "SELECT notice_ordinal,source_row_sha256" in sql:
                    self.current_all = ordered_rows
                else:
                    raise AssertionError(sql)

            def fetchone(self):
                return self.current_one

            def fetchall(self):
                return self.current_all

        cursor = ReconcileCursor()
        self.assertEqual(pilot._reconcile_committed_pilot(cursor, plan, plan_sha), payload)
        committed["receipt_sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "committed journal pilot evidence"):
            pilot._reconcile_committed_pilot(cursor, plan, plan_sha)

    def test_committed_retry_reconciles_without_schema_or_insert(self) -> None:
        _, counts = self.parse()
        plan = self.make_plan(counts)
        payload = pilot._receipt_payload(plan, "c" * 64, {"E": {"free_bytes": 20}})

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeConnection:
            def __init__(self):
                self.cursor_value = FakeCursor()
                self.commits = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def cursor(self):
                return self.cursor_value

            def commit(self):
                self.commits += 1

        connection = FakeConnection()

        @contextmanager
        def postgres_conn():
            yield connection

        insert = Mock()
        with (
            patch("app.db.postgres_conn", postgres_conn),
            patch.object(pilot, "_postgres_database_identity", return_value=self.postgres_target),
            patch.object(pilot, "_docker_postgres_identity", return_value=self.postgres_target),
            patch.object(pilot, "_relation_presence", return_value=["present"] * 5),
            patch.object(pilot, "_insert_new_pilot", insert),
            patch.object(pilot, "_reconcile_committed_pilot", return_value=payload),
        ):
            result, reconciled = pilot._database_apply_or_reconcile(
                [], plan, "c" * 64, self.storage_topology, None
            )

        self.assertEqual(result, payload)
        self.assertTrue(reconciled)
        insert.assert_not_called()
        self.assertEqual(connection.commits, 1)


if __name__ == "__main__":
    unittest.main()
