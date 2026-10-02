"""GB Madrid-IR governed pilot tests; no production mutation."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.global_trademarks import gb_madrid_ir_pilot as madrid


def topology() -> dict:
    return {
        "database": "markorbit",
        "system_identifier": "123456789",
        "server_address": "172.18.0.3",
        "server_port": 5432,
        "configured_host": "localhost",
        "configured_port": 5432,
        "container_id": "a" * 64,
        "container_image_sha256": "b" * 64,
        "compose_project": "markorbit-data-engine",
        "compose_service": "postgres",
        "compose_external_storage": True,
        "data_directory": "/var/lib/postgresql/data",
        "data_mount_type": "bind",
        "data_mount_source": r"E:\MarkOrbitData\production\postgres",
        "data_mount_host_drive": "E",
    }


def proof(root: Path, kinds: tuple[str, ...] = ("ACCEPTED", "QUARANTINED")) -> dict:
    rows = root / "rows.jsonl"
    bad = root / "bad.jsonl"
    accepted_payloads = []
    bad_payloads = []
    for ordinal in range(1, madrid.PILOT_SOURCE_ROWS + 2):
        kind = kinds[(ordinal - 1) % len(kinds)]
        if kind == "ACCEPTED":
            accepted_payloads.append(
                {
                    "source_row_ordinal": ordinal,
                    "source_stream": madrid.STREAM,
                    "source_archive_sha256": "2" * 64,
                    "source_member": "OpenDataIR.txt",
                    "application_number": f"WO{ordinal}",
                    "applicant_name_raw": "owner",
                    "source_status_raw": "status",
                    "nice_classes": [1],
                    "source_cells_sha256": f"{ordinal:064x}"[-64:],
                    "source_fields": {
                        "Trade Mark": f"WO{ordinal}",
                        "Name": "owner",
                        "Status": "status",
                    },
                    "historical_source_only": True,
                    "current_state_verified": False,
                }
            )
        else:
            bad_payloads.append(
                {
                    "source_row_ordinal": ordinal,
                    "source_stream": madrid.STREAM,
                    "source_archive_sha256": "2" * 64,
                    "source_member": "OpenDataIR.txt",
                    "reason": "FIELD_COUNT",
                    "cells": ["bad"],
                }
            )
    rows.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in accepted_payloads),
        encoding="utf-8",
    )
    bad.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in bad_payloads),
        encoding="utf-8",
    )
    return {
        "spec": {
            "zip_sha": "2" * 64,
            "member": "OpenDataIR.txt",
            "manifest_sha": "3" * 64,
            "total": 109695,
            "accepted": 109687,
            "bad": 8,
        },
        "manifest": {
            "accepted_jsonl_sha256": "4" * 64,
            "quarantine_jsonl_sha256": "5" * 64,
        },
        "rows": rows,
        "bad": bad,
        "e_stage": {
            "root": str(madrid.e_stage.STAGE_ROOT),
            "manifest_sha256": madrid.e_stage.STAGE_MANIFEST_SHA,
            "relocation_plan_sha256": madrid.e_stage.RELOCATION_PLAN_SHA,
            "relocation_receipt_sha256": madrid.e_stage.RELOCATION_RECEIPT_SHA,
            "independent_audit_sha256": madrid.e_stage.INDEPENDENT_AUDIT_SHA,
        },
    }


class GBMadridIRPilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def plan(self, evidence: dict, prefix: dict) -> dict:
        with patch.object(
            madrid.domestic,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 20, "reserve_bytes": 5}},
        ):
            return madrid.make_plan(
                evidence,
                prefix,
                postgres_topology=topology(),
                execution_main="a" * 40,
            )

    def test_prefix_summary_preserves_ordered_accepted_and_quarantine_rows(self) -> None:
        summary = madrid.prefix_summary(proof(self.root))
        self.assertEqual(summary["source_rows"], 1000)
        self.assertEqual(summary["accepted_rows"], 500)
        self.assertEqual(summary["quarantined_rows"], 500)
        self.assertRegex(summary["ordered_row_identity_sha256"], r"^[0-9a-f]{64}$")

    def test_plan_is_e_bound_historical_only_and_pilot_only(self) -> None:
        evidence = proof(self.root, ("ACCEPTED",))
        prefix = madrid.prefix_summary(evidence)
        plan = self.plan(evidence, prefix)
        self.assertEqual(plan["structured_stage_drive"], "E")
        self.assertEqual(plan["target_database_physical_drive"], "E")
        self.assertEqual(plan["future_query_storage_placement"], "hot_global")
        self.assertEqual(plan["postgres_topology"]["data_mount_source"][0], "E")
        self.assertEqual(plan["pilot_source_rows"], 1000)
        self.assertTrue(plan["historical_source_only"])
        self.assertFalse(plan["current_state_verified"])
        self.assertFalse(plan["full_madrid_import_authorized"])
        self.assertFalse(plan["journal_ingest_authorized"])
        self.assertFalse(plan["api_cutover_authorized"])
        self.assertEqual(plan["disk_reserve_gate"], {"E": {"reserve_bytes": 5}})
        self.assertNotIn("free_bytes", str(plan["disk_reserve_gate"]))

    def test_exact_authority_is_required(self) -> None:
        evidence = proof(self.root, ("ACCEPTED",))
        plan = self.plan(evidence, madrid.prefix_summary(evidence))
        digest = "b" * 64
        token = f"GO #855 GB-MADRID-IR-PILOT {digest} FIRST-1000-ONLY"
        with patch.dict(
            madrid.source_rows.SOURCE_SPECS,
            {madrid.STREAM: evidence["spec"]},
        ):
            madrid.authorize(plan, digest, token)
            with self.assertRaisesRegex(RuntimeError, "exact GB Madrid-IR"):
                madrid.authorize(plan, digest, token + " EXTRA")

    def test_live_prestate_requires_no_madrid_run_or_rows(self) -> None:
        evidence = proof(self.root, ("ACCEPTED",))
        empty = {
            "database": "markorbit",
            "run": None,
            "rows": {"total": 0, "unapproved_current": 0, "nonhistorical": 0},
        }
        with patch.object(madrid, "_readonly_live_prestate", return_value=empty):
            self.assertEqual(madrid.verify_live_prestate(evidence), empty)
        with patch.object(
            madrid,
            "_readonly_live_prestate",
            return_value={**empty, "rows": {**empty["rows"], "total": 1}},
        ):
            with self.assertRaisesRegex(RuntimeError, "prestate"):
                madrid.verify_live_prestate(evidence)

    def test_dynamic_free_space_does_not_change_plan(self) -> None:
        evidence = proof(self.root, ("ACCEPTED",))
        prefix = madrid.prefix_summary(evidence)
        with patch.object(
            madrid.domestic,
            "require_e_disk_reserve",
            side_effect=(
                {"E": {"free_bytes": 10, "reserve_bytes": 5}},
                {"E": {"free_bytes": 999, "reserve_bytes": 5}},
            ),
        ):
            first = madrid.make_plan(
                evidence,
                prefix,
                postgres_topology=topology(),
                execution_main="a" * 40,
            )
            second = madrid.make_plan(
                evidence,
                prefix,
                postgres_topology=topology(),
                execution_main="a" * 40,
            )
        self.assertEqual(first, second)

    def test_plan_binds_schema_and_operator_identities(self) -> None:
        evidence = proof(self.root, ("ACCEPTED",))
        plan = self.plan(evidence, madrid.prefix_summary(evidence))
        self.assertEqual(
            plan["schema_sql_sha256"],
            hashlib.sha256(madrid.source_rows.SCHEMA_SQL.encode()).hexdigest(),
        )
        self.assertEqual(
            plan["operator_sha256"],
            madrid.domestic.canonical_text_sha(Path(madrid.__file__)),
        )

    def test_authorized_rows_recheck_exact_ordered_digest(self) -> None:
        evidence = proof(self.root, ("ACCEPTED",))
        plan = self.plan(evidence, madrid.prefix_summary(evidence))
        madrid.authorized_pilot_rows(evidence, plan)
        rows = evidence["rows"].read_text(encoding="utf-8").splitlines()
        first = json.loads(rows[0])
        first["applicant_name_raw"] = "stage replaced after authorization"
        rows[0] = json.dumps(first, sort_keys=True)
        evidence["rows"].write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "drift"):
            madrid.authorized_pilot_rows(evidence, plan)

    def test_receipt_publication_recovers_after_post_commit_io_fault(self) -> None:
        receipt = self.root / "receipt.json"
        payload = {"plan_sha256": "c" * 64, "status": "COMMITTED"}
        with patch.object(madrid.safety.os, "link", side_effect=OSError("disk fault")):
            with self.assertRaisesRegex(OSError, "disk fault"):
                madrid.safety.atomic_publish_receipt(receipt, payload)
        self.assertFalse(receipt.exists())
        digest = madrid.safety.atomic_publish_receipt(receipt, payload)
        self.assertEqual(digest, hashlib.sha256(receipt.read_bytes()).hexdigest())
        self.assertEqual(madrid.safety.atomic_publish_receipt(receipt, payload), digest)

        receipt.write_bytes(b'{"partial":')
        with self.assertRaisesRegex(RuntimeError, "conflicts with DB evidence"):
            madrid.safety.atomic_publish_receipt(receipt, payload)
        repaired = madrid.safety.atomic_publish_receipt(
            receipt, payload, repair_from_db_evidence=True
        )
        self.assertEqual(receipt.read_bytes(), madrid.safety.receipt_bytes(payload))
        self.assertEqual(repaired, digest)

    def test_plan_freeze_is_atomic_verified_and_never_reused(self) -> None:
        path = self.root / "plan.json"
        payload = {"kind": "FROZEN_NO_APPLY", "nonce": "new-authority-only"}
        digest = madrid.safety.atomic_freeze_plan(path, payload)
        self.assertEqual(path.read_bytes(), madrid.safety.receipt_bytes(payload))
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaisesRegex(RuntimeError, "refuse reuse"):
            madrid.safety.atomic_freeze_plan(path, payload)

    def test_non_e_postgres_topology_is_rejected(self) -> None:
        value = topology()
        value["data_mount_source"] = r"D:\docker\volumes\postgres"
        value["data_mount_host_drive"] = "D"
        self.assertFalse(madrid.safety.validate_frozen_topology(value))

    def test_apply_topology_rejects_wrong_cluster_endpoint_and_placement(self) -> None:
        expected = topology()
        for changed in (
            {**expected, "system_identifier": "987654321"},
            {**expected, "configured_port": 5544},
            {
                **expected,
                "data_mount_source": r"D:\docker\postgres",
                "data_mount_host_drive": "D",
            },
        ):
            with (
                self.subTest(changed=changed),
                patch.object(
                    madrid.safety,
                    "capture_e_postgres_topology",
                    return_value=changed,
                ),
                self.assertRaisesRegex(RuntimeError, "topology or cluster endpoint drifted"),
            ):
                madrid.safety.verify_e_postgres_topology(object(), expected)

    def test_live_topology_binds_dsn_cluster_container_and_e_mount(self) -> None:
        inspect = {
            "Id": "a" * 64,
            "Image": "sha256:" + "b" * 64,
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "markorbit-data-engine",
                    "com.docker.compose.service": "postgres",
                    "com.docker.compose.project.config_files": (
                        r"D:\repo\docker-compose.yml,D:\repo\docker-compose.external-storage.yml"
                    ),
                }
            },
            "Mounts": [
                {
                    "Destination": "/var/lib/postgresql/data",
                    "Source": r"E:\MarkOrbitData\production\postgres",
                    "Type": "bind",
                    "RW": True,
                }
            ],
            "NetworkSettings": {
                "Ports": {"5432/tcp": [{"HostPort": "5432"}]},
                "Networks": {"default": {"IPAddress": "172.18.0.3"}},
            },
        }
        with (
            patch(
                "app.db.get_settings",
                return_value=SimpleNamespace(
                    postgres_host="localhost", postgres_port=5432, postgres_db="markorbit"
                ),
            ),
            patch.object(
                madrid.safety,
                "_docker_json",
                side_effect=([{"ID": "a" * 12}], [inspect]),
            ),
            patch.object(
                madrid.safety,
                "_postgres_database_identity",
                return_value={
                    "database": "markorbit",
                    "system_identifier": "123456789",
                    "server_address": "172.18.0.3",
                    "server_port": 5432,
                    "data_directory": "/var/lib/postgresql/data",
                },
            ),
        ):
            self.assertEqual(
                madrid.safety.capture_e_postgres_topology(object()),
                topology(),
            )
        inspect["Mounts"][0]["Source"] = r"D:\docker\postgres"
        with (
            patch(
                "app.db.get_settings",
                return_value=SimpleNamespace(
                    postgres_host="localhost", postgres_port=5432, postgres_db="markorbit"
                ),
            ),
            patch.object(
                madrid.safety,
                "_docker_json",
                side_effect=([{"ID": "a" * 12}], [inspect]),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "not E-backed"):
                madrid.safety.capture_e_postgres_topology(object())

    def test_committed_state_reconciliation_recomputes_exact_ordered_digest(self) -> None:
        evidence = proof(self.root, ("ACCEPTED",))
        plan = self.plan(evidence, madrid.prefix_summary(evidence))
        records = list(madrid.source_rows.ordered_source_records(evidence, madrid.STREAM))[
            : madrid.PILOT_SOURCE_ROWS
        ]

        class Cursor:
            def __init__(self, *, swap_first: bool = False) -> None:
                self.swap_first = swap_first
                self.query = ""

            def execute(self, query: str, _params: tuple) -> None:
                self.query = query

            def fetchall(self) -> list[dict]:
                rows = [
                    {
                        "source_row_ordinal": row[0],
                        "record_kind": row[1],
                        "source_row_sha256": row[-1],
                        "historical_source_only": True,
                        "current_state_verified": False,
                    }
                    for row in records
                ]
                if self.swap_first:
                    rows[0]["source_row_sha256"] = "f" * 64
                return rows

            def fetchone(self) -> dict:
                return {
                    "source_stream": madrid.STREAM,
                    "source_member": plan["source_member"],
                    "stage_manifest_sha256": plan["stage_manifest_sha256"],
                    "stage_rows_sha256": plan["stage_rows_sha256"],
                    "stage_quarantine_sha256": plan["stage_quarantine_sha256"],
                    "expected_source_rows": plan["source_total_rows"],
                    "expected_accepted_rows": plan["source_expected_accepted_rows"],
                    "expected_quarantine_rows": plan["source_expected_quarantine_rows"],
                    "checkpoint_source_ordinal": madrid.PILOT_SOURCE_ROWS,
                    "rows_committed": madrid.PILOT_SOURCE_ROWS,
                    "accepted_committed": madrid.PILOT_SOURCE_ROWS,
                    "quarantine_committed": 0,
                    "status": "RUNNING",
                }

        madrid._verify_committed_state(Cursor(), plan, plan["source_zip_sha256"])
        with self.assertRaisesRegex(RuntimeError, "committed state drift"):
            madrid._verify_committed_state(Cursor(swap_first=True), plan, plan["source_zip_sha256"])

    def test_ambiguous_commit_reconciles_and_retry_does_not_insert(self) -> None:
        evidence = proof(self.root, ("ACCEPTED",))
        plan = self.plan(evidence, madrid.prefix_summary(evidence))
        plan_sha = "c" * 64
        reserve = {"E": {"free_bytes": 20, "reserve_bytes": 5}}
        payload = madrid._receipt_payload(plan, plan_sha, reserve)
        receipt = self.root / "receipt.json"
        with (
            patch.object(madrid, "RECEIPT", receipt),
            patch.object(madrid.domestic, "require_live_clean_main"),
            patch.object(
                madrid.domestic,
                "verify_apply_disk_reserve",
                return_value=reserve,
            ),
            patch.object(
                madrid,
                "_reconcile_committed",
                side_effect=(None, payload),
            ) as reconcile,
            patch("app.db.postgres_conn", side_effect=ConnectionError("commit outcome unknown")),
        ):
            result = madrid.apply_pilot(evidence, plan, plan_sha)
        self.assertEqual(result["receipt_sha256"], madrid.safety.receipt_sha256(payload))
        self.assertEqual(reconcile.call_count, 2)

        no_insert = Mock(side_effect=AssertionError("retry attempted INSERT"))
        with (
            patch.object(madrid, "RECEIPT", receipt),
            patch.object(madrid.domestic, "require_live_clean_main"),
            patch.object(
                madrid.domestic,
                "verify_apply_disk_reserve",
                return_value=reserve,
            ),
            patch.object(madrid, "_reconcile_committed", return_value=payload),
            patch("app.db.postgres_conn", no_insert),
        ):
            retried = madrid.apply_pilot(evidence, plan, plan_sha)
        self.assertEqual(retried["receipt_sha256"], result["receipt_sha256"])
        no_insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
