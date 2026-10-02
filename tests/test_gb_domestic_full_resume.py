"""GB Domestic full-resume governance tests; no production mutation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
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


def topology():
    return {
        "accepted_storage_topology_evidence": {
            "contract_version": full.pg_target.STORAGE_TOPOLOGY_VERSION,
            "contract_sha256": "1" * 64,
            "gb_hot_placement": {"drive": "E", "placement": "hot_global"},
            "accepted_docker_e_receipt_sha256": full.pg_target.ACCEPTED_DOCKER_E_RECEIPT_SHA,
            "docker_desktop_data_root": str(full.pg_target.DOCKER_E_ROOT),
            "docker_data_vhdx": str(full.pg_target.DOCKER_E_DATA_VHDX),
            "docker_engine_version": "29.6.2",
            "docker_root_dir": "/var/lib/docker",
        },
        "postgres_target_evidence": {
            "database": "markorbit",
            "data_directory": "/var/lib/postgresql/data",
            "server_version_num": 170006,
            "server_port": 5432,
            "server_address": "172.18.0.3",
            "observed_server_port": 5432,
            "system_identifier": "123456789",
            "configured_endpoint_host": "localhost",
            "configured_endpoint_port": 5432,
            "container_id": "a" * 64,
            "container_image_id": "sha256:" + "b" * 64,
            "compose_project": "markorbit-data-engine",
            "container_addresses": ["172.18.0.3"],
            "volume_name": "markorbit-data-engine_postgres_data",
            "volume_source": "/var/lib/docker/volumes/markorbit-data-engine_postgres_data/_data",
        },
    }


class GBDomesticFullResumeTests(unittest.TestCase):
    def test_production_authority_requires_clean_live_origin_main(self):
        head = "a" * 40

        def clean_main(args):
            if args == ["status", "--porcelain=v1"]:
                return ""
            if args == ["rev-parse", "HEAD"]:
                return head
            if args == ["ls-remote", "origin", "refs/heads/main"]:
                return f"{head}\trefs/heads/main"
            self.fail(args)

        with patch.object(full, "_git", side_effect=clean_main):
            self.assertEqual(full.require_live_clean_main(head), head)

        with patch.object(full, "_git", return_value="dirty"):
            with self.assertRaisesRegex(RuntimeError, "clean worktree"):
                full.require_live_clean_main()

        def feature_branch(args):
            if args == ["status", "--porcelain=v1"]:
                return ""
            if args == ["rev-parse", "HEAD"]:
                return head
            if args == ["ls-remote", "origin", "refs/heads/main"]:
                return f"{'b' * 40}\trefs/heads/main"
            self.fail(args)

        with patch.object(full, "_git", side_effect=feature_branch):
            with self.assertRaisesRegex(RuntimeError, "live origin/main"):
                full.require_live_clean_main()

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
            plan = full.make_plan(proof(), live(), postgres_topology=topology())
        self.assertEqual(plan["status"], "FROZEN_NO_APPLY")
        self.assertRegex(plan["execution_main_sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(plan["start_checkpoint_source_ordinal"], 1000)
        self.assertEqual(plan["target_checkpoint_source_ordinal"], 1188992)
        self.assertEqual(plan["remaining_source_rows"], 1187992)
        self.assertEqual(plan["batch_size"], 5000)
        self.assertEqual(plan["structured_stage_drive"], "E")
        self.assertEqual(plan["target_database_physical_drive"], "E")
        self.assertEqual(plan["future_query_storage_placement"], "hot_global")
        self.assertEqual(
            plan["postgres_topology"]["accepted_storage_topology_evidence"][
                "accepted_docker_e_receipt_sha256"
            ],
            full.pg_target.ACCEPTED_DOCKER_E_RECEIPT_SHA,
        )
        self.assertTrue(
            plan["postgres_topology"]["postgres_target_evidence"]["volume_source"].startswith(
                "/var/lib/docker/volumes/"
            )
        )
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
            first = full.make_plan(proof(), live(), postgres_topology=topology())
        with patch.object(
            full,
            "require_e_disk_reserve",
            return_value={"E": {"free_bytes": 888, "reserve_bytes": 5}},
        ):
            second = full.make_plan(proof(), live(), postgres_topology=topology())
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
            plan = full.make_plan(proof(), live(), postgres_topology=topology())
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

        drift = topology()
        drift["accepted_storage_topology_evidence"]["docker_data_vhdx"] = (
            r"D:\DockerData\disk\docker_data.vhdx"
        )
        with self.assertRaisesRegex(RuntimeError, "frozen plan/operator mismatch"):
            full.authorize({**plan, "postgres_topology": drift}, plan_sha, token)

    def test_apply_rechecks_exact_topology_on_mutation_connection(self):
        expected = topology()
        with patch.object(full.pg_target, "capture", return_value=expected):
            self.assertEqual(full.pg_target.verify(object(), expected), expected)
        changed = topology()
        changed["postgres_target_evidence"]["system_identifier"] = "987654321"
        with (
            patch.object(full.pg_target, "capture", return_value=changed),
            self.assertRaisesRegex(RuntimeError, "topology or cluster endpoint drifted"),
        ):
            full.pg_target.verify(object(), expected)

    def test_postgres_target_requires_healthy_named_volume_and_exact_endpoint(self):
        database = {
            key: value
            for key, value in topology()["postgres_target_evidence"].items()
            if key
            not in {
                "configured_endpoint_host",
                "configured_endpoint_port",
                "container_id",
                "container_image_id",
                "compose_project",
                "container_addresses",
                "volume_name",
                "volume_source",
            }
        }
        inspected = {
            "Id": "a" * 64,
            "Image": "sha256:" + "b" * 64,
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "markorbit-data-engine",
                    "com.docker.compose.service": "postgres",
                }
            },
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Mounts": [
                {
                    "Destination": "/var/lib/postgresql/data",
                    "Source": (
                        "/var/lib/docker/volumes/markorbit-data-engine_postgres_data/_data"
                    ),
                    "Name": "markorbit-data-engine_postgres_data",
                    "Type": "volume",
                    "RW": True,
                }
            ],
            "NetworkSettings": {
                "Ports": {"5432/tcp": [{"HostPort": "5432"}]},
                "Networks": {"default": {"IPAddress": "172.18.0.3"}},
            },
        }
        settings = SimpleNamespace(
            postgres_host="localhost", postgres_port=5432, postgres_db="markorbit"
        )
        with (
            patch("app.config.get_settings", return_value=settings),
            patch.object(full.pg_target, "_docker_lines", return_value=["a" * 12]),
            patch.object(full.pg_target, "_docker_json", return_value=[inspected]),
        ):
            self.assertEqual(
                full.pg_target._docker_postgres_identity(database),
                topology()["postgres_target_evidence"],
            )

        inspected["Mounts"][0]["Type"] = "bind"
        with (
            patch("app.config.get_settings", return_value=settings),
            patch.object(full.pg_target, "_docker_lines", return_value=["a" * 12]),
            patch.object(full.pg_target, "_docker_json", return_value=[inspected]),
            self.assertRaisesRegex(RuntimeError, "runtime topology is not accepted"),
        ):
            full.pg_target._docker_postgres_identity(database)

    def test_batch_and_total_bound_are_fixed(self):
        self.assertEqual(full.START_CHECKPOINT, 1000)
        self.assertEqual(full.TARGET_SOURCE_ROWS, 1188992)
        self.assertEqual(full.BATCH_SIZE, 5000)
        self.assertLess(full.BATCH_SIZE, full.TARGET_SOURCE_ROWS - full.START_CHECKPOINT)


if __name__ == "__main__":
    unittest.main()
