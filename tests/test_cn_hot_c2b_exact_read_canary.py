"""#843 bounded C2b exact-read canary contract tests, no production access."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "scripts/run_cn_hot_c2b_exact_read_canary.py"
SPEC = importlib.util.spec_from_file_location("cn_hot_c2b_exact_read_canary", FILE)
assert SPEC and SPEC.loader
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


def result_json(*, rows_read=1024, bytes_read=2048, num_rows=1):
    meta = [
        {
            "name": column,
            "type": "UInt64"
            if column == "source_rank"
            else "Array(UInt8)"
            if column == "classes"
            else "String",
        }
        for column in canary.EXPECTED_COLUMNS
    ]
    data = []
    for _ in range(num_rows):
        row = ["source"] * len(meta)
        row[canary.EXPECTED_COLUMNS.index("source_rank")] = "2000202407000064"
        row[canary.EXPECTED_COLUMNS.index("classes")] = [25]
        data.append(row)
    return json.dumps(
        {
            "meta": meta,
            "data": data,
            "statistics": {"rows_read": rows_read, "bytes_read": bytes_read},
        }
    )


class C2bExactReadCanaryTests(unittest.TestCase):
    def test_query_is_identical_bounded_route_sql(self):
        sql = (
            "SELECT case_id FROM markorbit_facts.cn_case_current FINAL "
            "WHERE application_number = '10002014' LIMIT 2"
        )
        bounded = canary.capped_sql(sql)
        self.assertIn("max_rows_to_read=32768", bounded)
        self.assertIn("max_bytes_to_read=33554432", bounded)
        self.assertIn("max_execution_time=10", bounded)
        self.assertIn("max_memory_usage=268435456", bounded)
        self.assertIn("FORMAT JSONCompact", bounded)
        for invalid in (
            "DROP TABLE markorbit_facts.cn_case_current",
            "SELECT * FROM markorbit_facts.cn_case_current LIMIT 2",
            sql.replace("LIMIT 2", "LIMIT 3"),
            sql + ";",
        ):
            with self.subTest(sql=invalid), self.assertRaises(RuntimeError):
                canary.capped_sql(invalid)

    def test_real_multiline_route_sql_is_accepted_by_preflight_guard(self):
        sys.path.insert(0, str(FILE.parents[1]))
        from app.cn.discovery_preliminary_publication import (
            PreliminaryPublicationDiscoveryRequest,
            build_page_sql,
        )

        for key in ("10002014", '"10002014"'):
            with self.subTest(raw_application_number=key):
                req = PreliminaryPublicationDiscoveryRequest(
                    application_number_start=key,
                    application_number_end=key + "!",
                    page_size=1,
                    cursor=None,
                )
                actual = build_page_sql(req, fetch_limit=2)
                self.assertRegex(actual, r"SELECT\s*\n")
                bounded = canary.capped_sql(actual)
                self.assertIn("FROM markorbit_facts.cn_case_current FINAL", bounded)
                self.assertIn("max_rows_to_read=32768", bounded)
                self.assertTrue(bounded.endswith(" FORMAT JSONCompact"))
                with self.assertRaisesRegex(RuntimeError, "exact capped"):
                    canary.capped_sql(actual.replace("LIMIT 2", "UNION ALL SELECT 1 LIMIT 2"))

    def test_client_decodes_uint64_without_changing_original_key(self):
        base = {
            "max_rows_to_read": 250000,
            "max_bytes_to_read": 268435456,
            "read_overflow_mode": "throw",
        }
        with patch.object(canary, "runner", return_value=result_json()) as mock:
            client = canary.FrozenReadClient("target")
            result = client.query(
                "SELECT case_id FROM markorbit_facts.cn_case_current FINAL LIMIT 2",
                settings=base,
            )
        self.assertEqual(result.column_names, list(canary.EXPECTED_COLUMNS))
        row = result.result_rows[0]
        self.assertEqual(type(row[-1]), int)
        self.assertEqual(row[-1], 2000202407000064)
        self.assertEqual(row[canary.EXPECTED_COLUMNS.index("classes")], [25])
        self.assertEqual(
            client.last_read_statistics,
            {
                "rows_read": 1024,
                "bytes_read": 2048,
            },
        )
        self.assertIn("wsl.exe", mock.call_args.args[0][0])
        with self.assertRaises(RuntimeError):
            client.insert([])
        with self.assertRaises(RuntimeError):
            client.command("OPTIMIZE TABLE x")

    def test_fail_closed_on_actual_stats_and_api_budget(self):
        base = {
            "max_rows_to_read": 250000,
            "max_bytes_to_read": 268435456,
            "read_overflow_mode": "throw",
        }
        for payload in (
            result_json(rows_read=32769),
            result_json(bytes_read=33554433),
            result_json(num_rows=3),
        ):
            with (
                self.subTest(payload=payload[:55]),
                patch.object(canary, "runner", return_value=payload),
            ):
                with self.assertRaises(RuntimeError):
                    canary.FrozenReadClient("source").query(
                        "SELECT case_id FROM markorbit_facts.cn_case_current FINAL LIMIT 2",
                        settings=base,
                    )
        with self.assertRaisesRegex(RuntimeError, "source route"):
            canary.FrozenReadClient("source").query(
                "SELECT 1", settings={"read_overflow_mode": "break"}
            )

    def test_pairwise_route_comparison_fail_closed(self):
        sys.path.insert(0, str(FILE.parents[1]))
        from app.cn import discovery_preliminary_publication as discovery

        frozen = {
            "processing_packages": 0,
            "successful_packages": 86,
            "max_success_sequence": 2993,
            "coverage_date": "2026-08-31",
            "running_jobs": 0,
            "watermark": "2026-08-31:2993:86",
        }
        plan = {
            "control_epoch": frozen,
            "read_canary": {
                "sample_ranges": [
                    {
                        "start_inclusive": key,
                        "end_exclusive": key + "!",
                        "page_size": 1,
                        "cursor": None,
                    }
                    for key in ("10002014", '"10002014"')
                ]
            },
        }

        class FakeClient:
            def __init__(self, backend):
                self.backend = backend
                self.last_read_statistics = {"rows_read": 1, "bytes_read": 64}

        def result(request, *, client, **_kwargs):
            return {
                "results": [{"application_number": request.application_number_start}],
                "next_cursor": None,
                "query": {"same": True},
            }

        with (
            patch.object(canary, "FrozenReadClient", FakeClient),
            patch.object(discovery, "execute_page", side_effect=result),
        ):
            proofs = canary.compare_exact_route(plan, lambda: dict(frozen))
        self.assertEqual(len(proofs), 2)
        self.assertTrue(all(p["route_output_equal"] for p in proofs))
        self.assertNotEqual(
            proofs[0]["full_route_response_sha256"], proofs[1]["full_route_response_sha256"]
        )

        def mismatch(request, *, client, **_kwargs):
            page = result(request, client=client)
            if client.backend == "target":
                page["query"] = {"same": False}
            return page

        with (
            patch.object(canary, "FrozenReadClient", FakeClient),
            patch.object(discovery, "execute_page", side_effect=mismatch),
        ):
            with self.assertRaisesRegex(RuntimeError, "parity not accepted"):
                canary.compare_exact_route(plan, lambda: dict(frozen))

    def test_auth_token_is_exact_not_a_go_for_cutover(self):
        self.assertEqual(
            canary.PLAN_SHA, "69052db6e09b11eba457aabb4bee9d89f4506659621103bf069d1a98860dc1ba"
        )
        self.assertIn("EXACT-KEY-FINAL-PARITY-READONLY", canary.AUTHORITY)
        self.assertNotIn("START-MERGES", canary.AUTHORITY)
        with patch.object(
            sys,
            "argv",
            [
                "canary",
                "--repo",
                str(FILE.parents[1]),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "authority token"):
                canary.main()

    def test_epoch_reader_rechecks_every_use(self):
        sys.path.insert(0, str(FILE.parents[1]))
        frozen = {
            "processing_packages": 0,
            "successful_packages": 86,
            "max_success_sequence": 2993,
            "coverage_date": "2026-08-31",
            "running_jobs": 0,
            "watermark": "2026-08-31:2993:86",
        }
        reader = canary.make_epoch_reader(frozen, lambda: dict(frozen))
        self.assertEqual(reader().max_success_sequence, 2993)
        reader = canary.make_epoch_reader(frozen, lambda: dict(frozen, max_success_sequence=2994))
        with self.assertRaisesRegex(RuntimeError, "epoch changed"):
            reader()


if __name__ == "__main__":
    unittest.main()
