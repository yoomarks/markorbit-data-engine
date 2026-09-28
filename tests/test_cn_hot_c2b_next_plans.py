"""#843 C2b follow-on plans: fail-closed unit tests, no database required."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/freeze_cn_hot_c2b_next_plans.py"
SPEC = importlib.util.spec_from_file_location("cn_hot_c2b_next_plans", SCRIPT)
assert SPEC and SPEC.loader
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)


def accepted_facts():
    read_routes = {
        f"route-{i}": {
            "dependencies": ["source_only"],
            "missing_target_tables": ["source_only"],
            "serving_cutover_authorized": False,
        }
        for i in range(8)
    }
    read_routes[plan.ROUTE] = {
        "dependencies": [plan.TABLE],
        "missing_target_tables": [],
        "serving_cutover_authorized": False,
    }
    gate = {
        "status": "CUTOVER_NOT_AUTHORIZED",
        "r9_final_receipt_sha256": plan.R9_SHA,
        "api_binding": "SOURCE_CLICKHOUSE",
        "api_writers_stopped": True,
        "source_and_target_idle": True,
        "six_accepted_total_rows": 1908623840,
        "routes": read_routes,
        "write_routes": {
            f"/api/admin/writer-{i}": {"target_write_authorized": False} for i in range(3)
        },
        "capacity": {
            "hot_cn": {"total": 1081101176832, "free": 521296957440},
            "D": {"total": 2048390066176, "free": 1402318360576, "floor_30pct": 614517019853},
            "E": {"total": 2048391114752, "free": 738985959424, "floor_30pct": 614517334426},
            "large_single_partitions": {
                "cn_goods_item_current": {
                    "bytes": 233881500902,
                    "partition": "tuple()",
                },
                "cn_observed_event": {
                    "bytes": 131919887426,
                    "partition": "tuple()",
                },
            },
        },
    }
    control = {
        "processing_packages": 0,
        "successful_packages": 86,
        "max_success_sequence": 2993,
        "coverage_date": "2026-08-31",
        "running_jobs": 0,
        "watermark": "2026-08-31:2993:86",
    }
    samples = [
        {
            "key": key,
            "raw_hex": key.encode().hex().upper(),
            "source_physical_versions_seen": 2 if key == "10002014" else 1,
            "target_physical_versions_seen": 1,
            "latest_source_equals_target_raw": True,
            "preliminary_publication_date": "2012-10-06",
            "has_literal_quote": key.startswith('"'),
            "is_route_final_parity": False,
        }
        for key in plan.SAMPLE_KEYS
    ]
    return gate, control, samples


class C2bNextPlanTests(unittest.TestCase):
    def make(self, *, gate=None, control=None, samples=None):
        g, c, s = accepted_facts()
        return plan.build_plan(
            gate or g,
            control or c,
            samples or s,
            plan.PRIOR_SHA,
            "verified-auditor-sha",
        )

    def test_postgres_epoch_queries_use_valid_read_only_count_star(self):
        with patch.object(plan, "run", side_effect=["0|86|2993|2026-08-31", "0"]) as call:
            result = plan.epoch()
        self.assertEqual(result["watermark"], "2026-08-31:2993:86")
        self.assertEqual(result["processing_packages"], 0)
        self.assertIn("SELECT count(*) FROM control.job_run", call.call_args_list[1].args[0][-1])

    def test_paired_exact_keys_are_not_conflated(self):
        self.assertEqual(plan.exact_key_sql("10002014"), "'10002014'")
        self.assertIn("unhex('22')", plan.exact_key_sql('"10002014"'))
        self.assertNotEqual(*[plan.exact_key_sql(k) for k in plan.SAMPLE_KEYS])
        with self.assertRaises(RuntimeError):
            plan.exact_key_sql("10002015")
        for key in plan.SAMPLE_KEYS:
            sql = plan.sample_sql(key)
            self.assertNotIn(" FINAL ", sql)
            self.assertIn("max_rows_to_read=32768", sql)
            self.assertIn("read_overflow_mode='throw'", sql)

    def test_plan_has_no_apply_and_strict_merge_envelope(self):
        result = self.make()
        self.assertEqual(result["status"], "FROZEN_READ_ONLY_PLANS_NO_APPLY")
        canary = result["read_canary"]
        self.assertFalse(canary["probe_allowed_now"])
        self.assertFalse(canary["serving_route_activation_authorized"])
        self.assertEqual(canary["sample_application_numbers"], list(plan.SAMPLE_KEYS))
        self.assertTrue(
            all(r["end_exclusive"] == r["start_inclusive"] + "!" for r in canary["sample_ranges"])
        )
        merge = result["merge_capacity"]
        self.assertEqual(merge["proposed_input_parts_cap_bytes"], 16 * plan.GIB)
        self.assertEqual(merge["proposed_serial_peak_scratch_cap_bytes"], 32 * plan.GIB)
        self.assertGreater(
            merge["free_above_30pct_floor_bytes"],
            merge["proposed_serial_peak_scratch_cap_bytes"] + plan.BUFFER,
        )
        self.assertFalse(merge["goods_whole_partition_final_admitted"])
        self.assertFalse(merge["start_merges_or_optimize_authorized"])
        self.assertFalse(result["production_mutation_performed"])

    def test_fail_closed_on_epoch_or_writer_drift(self):
        gate, control, samples = accepted_facts()
        for field, invalid in (
            ("processing_packages", 1),
            ("running_jobs", 1),
            ("max_success_sequence", 0),
            ("coverage_date", ""),
        ):
            changed = dict(control)
            changed[field] = invalid
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.make(control=changed)
        for field, invalid in (
            ("api_writers_stopped", False),
            ("source_and_target_idle", False),
            ("api_binding", "TARGET"),
            ("six_accepted_total_rows", 1),
        ):
            changed = dict(gate)
            changed[field] = invalid
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.make(gate=changed)

    def test_fail_closed_on_route_or_capacity(self):
        gate, _, _ = accepted_facts()
        changed = dict(gate)
        changed["routes"] = dict(gate["routes"])
        changed["routes"][plan.ROUTE] = {
            "dependencies": ["cn_case_current", "cn_observed_event"],
            "missing_target_tables": [],
            "serving_cutover_authorized": False,
        }
        with self.assertRaisesRegex(RuntimeError, "route no longer"):
            self.make(gate=changed)
        changed = dict(gate)
        changed["capacity"] = dict(gate["capacity"])
        changed["capacity"]["E"] = dict(
            gate["capacity"]["E"], free=gate["capacity"]["E"]["floor_30pct"] - 1
        )
        with self.assertRaisesRegex(RuntimeError, "reserve"):
            self.make(gate=changed)

    def test_latest_source_not_same_as_target_fails_closed(self):
        _, _, samples = accepted_facts()
        incorrect = [dict(item) for item in samples]
        incorrect[0]["latest_source_equals_target_raw"] = False
        with self.assertRaisesRegex(RuntimeError, "sample evidence"):
            self.make(samples=incorrect)

    def test_exact_key_probe_requires_raw_latest_parity(self):
        source_latest = (
            "3130303032303134",
            "2012-10-06",
            "0",
            "2000202407000064",
            "A" * 64,
            "B" * 64,
            "caseid",
        )
        older = (*source_latest[:3], "1000000000000013", *source_latest[4:])
        quoted = ('"10002014"'.encode().hex().upper(), *source_latest[1:])

        def fake_probe(key, *, target):
            if key == "10002014":
                return [source_latest] if target else [source_latest, older]
            return [quoted]

        with patch.object(plan, "probe", side_effect=fake_probe):
            result = plan.accepted_sample_proof()
        self.assertEqual([r["source_physical_versions_seen"] for r in result], [2, 1])
        self.assertEqual([r["has_literal_quote"] for r in result], [False, True])

        def bad_probe(key, *, target):
            if key == "10002014" and target:
                return [older]
            return fake_probe(key, target=target)

        with patch.object(plan, "probe", side_effect=bad_probe):
            with self.assertRaisesRegex(RuntimeError, "parity failed"):
                plan.accepted_sample_proof()

    def test_probe_rejects_missing_preliminary_date(self):
        row = ("3130303032303134", r"\N", "0", "2000202407000064", "A" * 64, "B" * 64, "caseid")
        with patch.object(plan, "probe", return_value=[row]):
            with self.assertRaisesRegex(RuntimeError, "preliminary date"):
                plan.accepted_sample_proof()


if __name__ == "__main__":
    unittest.main()
