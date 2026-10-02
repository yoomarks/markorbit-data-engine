"""Madrid-IR full-resume governance tests; no production mutation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.global_trademarks import gb_madrid_ir_full_resume as full


def proof() -> dict:
    return {
        "spec": {
            "zip_sha": full.source_rows.SOURCE_SPECS[full.STREAM]["zip_sha"],
            "member": "OpenDataIR.txt",
            "manifest_sha": full.source_rows.SOURCE_SPECS[full.STREAM]["manifest_sha"],
            "total": full.TARGET_SOURCE_ROWS,
            "accepted": 109687,
            "bad": 8,
        },
        "manifest": {
            "accepted_jsonl_sha256": "a" * 64,
            "quarantine_jsonl_sha256": "b" * 64,
        },
        "e_stage": {
            "root": str(full.e_stage.STAGE_ROOT),
            "manifest_sha256": full.e_stage.STAGE_MANIFEST_SHA,
            "relocation_plan_sha256": full.e_stage.RELOCATION_PLAN_SHA,
            "relocation_receipt_sha256": full.e_stage.RELOCATION_RECEIPT_SHA,
            "independent_audit_sha256": full.e_stage.INDEPENDENT_AUDIT_SHA,
        },
    }


def topology() -> dict:
    return {
        "accepted_storage_topology_evidence": {
            "contract_version": full.safety.STORAGE_TOPOLOGY_VERSION,
            "contract_sha256": "1" * 64,
            "gb_hot_placement": {"drive": "E", "placement": "hot_global"},
            "accepted_docker_e_receipt_sha256": full.safety.ACCEPTED_DOCKER_E_RECEIPT_SHA,
            "docker_desktop_data_root": str(full.safety.DOCKER_E_ROOT),
            "docker_data_vhdx": str(full.safety.DOCKER_E_DATA_VHDX),
            "docker_engine_version": "29.6.2",
            "docker_root_dir": "/var/lib/docker",
        },
        "postgres_target_evidence": {
            "database": "markorbit",
            "data_directory": "/var/lib/postgresql/data",
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
            "volume_source": ("/var/lib/docker/volumes/markorbit-data-engine_postgres_data/_data"),
        },
    }


def summary() -> dict:
    return {
        "source_rows": full.TARGET_SOURCE_ROWS,
        "accepted_rows": 109687,
        "quarantined_rows": 8,
        "ordered_row_identity_sha256": "e" * 64,
    }


def plan() -> dict:
    with patch.object(
        full.domestic,
        "require_e_disk_reserve",
        return_value={"E": {"free_bytes": 10, "reserve_bytes": 5}},
    ):
        return full.make_plan(
            proof(),
            summary(),
            postgres_topology=topology(),
            execution_main="f" * 40,
        )


class GBMadridIRFullResumeTests(unittest.TestCase):
    def test_plan_binds_accepted_pilot_and_historical_semantics(self) -> None:
        value = plan()
        self.assertEqual(value["start_checkpoint_source_ordinal"], 1000)
        self.assertEqual(value["target_checkpoint_source_ordinal"], 109695)
        self.assertEqual(value["remaining_source_rows"], 108695)
        self.assertEqual(value["batch_size"], 5000)
        self.assertEqual(value["pilot_plan_sha256"], full.PILOT_PLAN_SHA)
        self.assertEqual(value["pilot_receipt_sha256"], full.PILOT_RECEIPT_SHA)
        self.assertEqual(value["target_database_physical_drive"], "E")
        self.assertEqual(value["future_query_storage_placement"], "hot_global")
        self.assertTrue(value["historical_source_only"])
        self.assertFalse(value["current_state_verified"])
        self.assertFalse(value["journal_ingest_authorized"])
        self.assertFalse(value["api_cutover_authorized"])
        self.assertFalse(value["clickhouse_cutover_authorized"])
        self.assertFalse(value["source_cleanup_authorized"])
        self.assertEqual(value["disk_reserve_gate"], {"E": {"reserve_bytes": 5}})

    def test_exact_full_resume_authority_is_required(self) -> None:
        value = plan()
        plan_sha = "1" * 64
        token = "GO #855 GB-MADRID-IR-FULL-RESUME " + plan_sha + " CHECKPOINT-1000-TO-109695"
        full.authorize(value, plan_sha, token)
        with self.assertRaisesRegex(RuntimeError, "exact GB Madrid-IR"):
            full.authorize(value, plan_sha, token + " EXTRA")
        with self.assertRaisesRegex(RuntimeError, "frozen plan/operator mismatch"):
            full.authorize({**value, "current_state_verified": True}, plan_sha, token)

    def test_initial_checkpoint_must_be_exact_accepted_pilot(self) -> None:
        state = {"checkpoint_source_ordinal": 1000, "status": "RUNNING"}
        with patch.object(full, "read_live_state", return_value=state):
            self.assertEqual(full.verify_initial_checkpoint(proof()), state)
        with (
            patch.object(
                full,
                "read_live_state",
                return_value={"checkpoint_source_ordinal": 1001, "status": "RUNNING"},
            ),
            self.assertRaisesRegex(RuntimeError, "exact accepted pilot checkpoint"),
        ):
            full.verify_initial_checkpoint(proof())

    def test_state_validation_accepts_resume_checkpoint_and_rejects_digest_drift(self) -> None:
        prefix = {
            "source_rows": 5000,
            "accepted_rows": 4999,
            "quarantined_rows": 1,
            "ordered_row_identity_sha256": "2" * 64,
        }
        state = {
            "source_stream": full.STREAM,
            "source_member": "OpenDataIR.txt",
            "stage_manifest_sha256": proof()["spec"]["manifest_sha"],
            "stage_rows_sha256": "a" * 64,
            "stage_quarantine_sha256": "b" * 64,
            "expected_source_rows": 109695,
            "expected_accepted_rows": 109687,
            "expected_quarantine_rows": 8,
            "checkpoint_source_ordinal": 5000,
            "rows_committed": 5000,
            "accepted_committed": 4999,
            "quarantine_committed": 1,
            "status": "RUNNING",
            "total": 5000,
            "min_ordinal": 1,
            "max_ordinal": 5000,
            "accepted": 4999,
            "quarantined": 1,
            "unapproved_current": 0,
            "nonhistorical": 0,
            "ordered_row_identity_sha256": "2" * 64,
        }
        with patch.object(full, "source_summary", return_value=prefix):
            self.assertEqual(full._validate_state(proof(), state), state)
            with self.assertRaisesRegex(RuntimeError, "checkpoint/residency/currentness"):
                full._validate_state(proof(), {**state, "ordered_row_identity_sha256": "3" * 64})

    def test_terminal_checkpoint_may_be_running_only_during_finalization(self) -> None:
        terminal = {
            "source_stream": full.STREAM,
            "source_member": "OpenDataIR.txt",
            "stage_manifest_sha256": proof()["spec"]["manifest_sha"],
            "stage_rows_sha256": "a" * 64,
            "stage_quarantine_sha256": "b" * 64,
            "expected_source_rows": 109695,
            "expected_accepted_rows": 109687,
            "expected_quarantine_rows": 8,
            "checkpoint_source_ordinal": 109695,
            "rows_committed": 109695,
            "accepted_committed": 109687,
            "quarantine_committed": 8,
            "status": "RUNNING",
            "total": 109695,
            "min_ordinal": 1,
            "max_ordinal": 109695,
            "accepted": 109687,
            "quarantined": 8,
            "unapproved_current": 0,
            "nonhistorical": 0,
            "ordered_row_identity_sha256": "4" * 64,
        }
        expected = {**summary(), "ordered_row_identity_sha256": "4" * 64}
        with patch.object(full, "source_summary", return_value=expected):
            full._validate_state(proof(), terminal, allow_terminal_running=True)
            with self.assertRaisesRegex(RuntimeError, "checkpoint/residency/currentness"):
                full._validate_state(proof(), terminal)

    def test_source_summary_is_ordered_and_includes_quarantine(self) -> None:
        rows = [
            (
                ordinal,
                "QUARANTINED" if ordinal == 1000 else "ACCEPTED",
                None,
                {},
                [],
                "c",
                f"{ordinal:064x}",
            )
            for ordinal in range(1, 1002)
        ]
        with patch.object(full.source_rows, "ordered_source_records", return_value=iter(rows)):
            value = full.source_summary(proof(), 1000)
        self.assertEqual(value["source_rows"], 1000)
        self.assertEqual(value["accepted_rows"], 999)
        self.assertEqual(value["quarantined_rows"], 1)
        self.assertRegex(value["ordered_row_identity_sha256"], r"^[0-9a-f]{64}$")

    def test_batch_size_is_bounded(self) -> None:
        self.assertLess(full.BATCH_SIZE, full.TARGET_SOURCE_ROWS - full.START_CHECKPOINT)
        self.assertEqual(full.BATCH_SIZE, 5000)


if __name__ == "__main__":
    unittest.main()
