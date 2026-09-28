"""Fail-closed tests for the read-only #843 CN Hot C2b gate."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "scripts" / "audit_cn_hot_c2b_readiness.py"
SPEC = importlib.util.spec_from_file_location("cn_hot_c2b_readiness", FILE)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

ACCEPTED = {
    "cn_goods_scope_lifecycle_current": 92573317,
    "cn_case_current": 89295453,
    "cn_case_scope_current": 92573175,
    "cn_case_party_current": 190705020,
    "cn_observed_event": 417986078,
    "cn_goods_item_current": 1025490797,
}


def facts():
    source = {n for deps in gate.ROUTES.values() for n in deps if not n.startswith("*")}
    source.update(gate.SIX)
    target = set(gate.SIX) | {"schema_version"}
    plan = {
        "version": "HOT_CN_NULL_SAFE_TWO_TABLE_MIGRATION_PLAN_V9",
        "main_sha": "3ebfb23fbe4fc6fae76c1bc6dd5fca264b87a280",
        "accepted_tables": [
            {"name": name, "source_final_rows": ACCEPTED[name]} for name in gate.SIX[:4]
        ],
    }
    final = {
        "status": "PASS",
        "r9_plan_sha256": gate.R9_PLAN_SHA,
        "source_retained": True,
        "source_cleanup_performed": False,
        "api_writers_stopped": True,
        "all_six_target_raw_current_rows": sum(ACCEPTED.values()),
        "completed_remaining_tables": [
            {"table": name, "accepted_rows": ACCEPTED[name]} for name in gate.SIX[4:]
        ],
    }
    physical = {
        name: {"parts": 2, "rows": count, "bytes": count * 10, "disk": "hot_cn"}
        for name, count in ACCEPTED.items()
    }
    partitions = {
        name: {
            "partition": "tuple()",
            "active_parts": 2,
            "bytes": count * 10,
            "largest_part_bytes": 1000,
        }
        for name, count in ACCEPTED.items()
        if name in gate.EXPENSIVE
    }
    disks = {
        "hot_cn": {"total": 1081101176832, "free": 521296957440},
        "D": {"total": 2048390066176, "free": 1402349391872, "floor_30pct": 614517019853},
        "E": {"total": 2048391114752, "free": 738985959424, "floor_30pct": 614517334426},
    }
    return dict(
        plan=plan,
        final=final,
        source=source,
        target=target,
        physical=physical,
        partitions=partitions,
        disks=disks,
        source_idle=True,
        target_idle=True,
        api_source_binding=True,
        code_hashes={"app/main_core.py": "frozen-test"},
    )


class C2bReadinessTests(unittest.TestCase):
    def test_live_route_deps_guard_covers_every_cn_get_route(self):
        repo = FILE.parents[1]
        hashes = gate.verify_route_inventory(repo)
        self.assertIn("app/integration_api.py", hashes)
        self.assertIn("app/main_core.py", hashes)

    def test_six_table_data_acceptance_is_not_cn_route_cutover(self):
        report = gate.make_report(**facts())
        self.assertEqual(report["status"], "CUTOVER_NOT_AUTHORIZED")
        self.assertEqual(report["six_accepted_total_rows"], 1908623840)
        self.assertFalse(report["global_api_endpoint_cutover_authorized"])
        case = report["routes"]["/api/v1/cn/cases/{application_number}"]
        self.assertIn("cn_case_relation_current", case["missing_target_tables"])
        agents = report["routes"]["/api/v1/cn/agents/by-name"]
        self.assertIn("cn_agent_current", agents["missing_target_tables"])
        self.assertFalse(report["compaction_authorized"])
        self.assertEqual(len(report["write_routes"]), 3)
        self.assertTrue(
            all(not value["target_write_authorized"] for value in report["write_routes"].values())
        )

    def test_all_tables_present_still_requires_read_canary_and_epoch(self):
        data = facts()
        data["target"] = set(data["source"])
        report = gate.make_report(**data)
        self.assertTrue(
            all(not entry["serving_cutover_authorized"] for entry in report["routes"].values())
        )
        self.assertEqual(
            report["routes"]["/api/v1/cn/cases/{application_number}"]["decision"],
            "SOURCE_ONLY_PENDING_EPOCH_AND_BOUNDED_READ_PROOF",
        )

    def test_fail_closed_for_unaccepted_source_or_target(self):
        for field in ("source_idle", "target_idle", "api_source_binding"):
            data = facts()
            data[field] = False
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                gate.make_report(**data)
        data = facts()
        data["physical"]["cn_goods_item_current"]["rows"] -= 1
        with self.assertRaisesRegex(RuntimeError, "accepted rows drift"):
            gate.make_report(**data)
        data = facts()
        data["source"].remove("cn_case_relation_current")
        with self.assertRaisesRegex(RuntimeError, "route source dependencies missing"):
            gate.make_report(**data)

    def test_physical_reserve_is_mandatory(self):
        data = facts()
        data["disks"]["E"]["free"] = data["disks"]["E"]["floor_30pct"] - 1
        with self.assertRaisesRegex(RuntimeError, "below 30%"):
            gate.make_report(**data)

    def test_metadata_only_query_contract(self):
        for sql in (
            "DROP TABLE x",
            "SELECT * FROM cn_case_current FINAL",
            "SELECT 1; DROP TABLE x",
        ):
            with self.subTest(sql=sql), self.assertRaises(RuntimeError):
                gate.select(sql, target=True)


if __name__ == "__main__":
    unittest.main()
