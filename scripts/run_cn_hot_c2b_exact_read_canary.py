"""Exact-authority, read-only CN preliminary-publication route parity canary.

No schema/data mutation, global endpoint change, API startup, FINAL table scan,
merge control, source cleanup or unattended retry. Only two frozen exact keys.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PLAN_SHA = "69052db6e09b11eba457aabb4bee9d89f4506659621103bf069d1a98860dc1ba"
ROUTE_SHA = "4953d0193b8f218b658ce7a01c1d565e3662401aed0e2f5fb9754fe378c1155d"
EPOCH_CODE_SHA = "9b02d3995343c7dd9d16e604553c5ed6b7e5aba43bbcbf6f5e14a926599e0ab0"
PLANNER_SHA = "bb24cf098e256effcd2ea34d7b8d3fdd8948b85798b56d0690e047b0ea9d3308"
PLAN_PATH = Path(r"D:\yoomarks\governed-plans\843\cn-hot-c2b-next-plans-r3.json")
R9_EVIDENCE = Path(r"D:\yoomarks\governed-plans\837\phase-c2-hot-cn-migration")
RECEIPT = Path(r"D:\yoomarks\governed-plans\843\cn-hot-c2b-exact-read-canary-r1.json")
AUTHORITY = "GO #843 CN-HOT-READ-CANARY " + PLAN_SHA + " EXACT-KEY-FINAL-PARITY-READONLY"
LIMITS = {
    "max_threads": 1,
    "max_rows_to_read": 32768,
    "max_bytes_to_read": 33554432,
    "max_execution_time": 10,
    "read_overflow_mode": "throw",
    "max_result_rows": 2,
    "max_memory_usage": 268435456,
}
EXPECTED_COLUMNS = (
    "case_id",
    "application_number",
    "mark_name_raw",
    "classes",
    "filing_date",
    "prelim_pub_date",
    "prelim_pub_issue",
    "source_effective_date",
    "source_package_id",
    "source_row_hash",
    "record_hash",
    "source_rank",
)


def require(ok: bool, why: str) -> None:
    if not ok:
        raise RuntimeError(why)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runner(argv: list[str], timeout: int = 25) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    require(
        result.returncode == 0,
        argv[0]
        + " read-only query failed (rc="
        + str(result.returncode)
        + "): "
        + result.stderr[-270:],
    )
    return result.stdout.strip()


def cli(backend: str) -> list[str]:
    require(backend in ("source", "target"), "invalid canary backend")
    if backend == "source":
        return [
            "docker.exe",
            "exec",
            "markorbit-data-engine-clickhouse-1",
            "clickhouse-client",
            "--query",
        ]
    return [
        "wsl.exe",
        "-d",
        "MarkOrbit-ClickHouse",
        "-u",
        "root",
        "--",
        "clickhouse",
        "client",
        "--host",
        "127.0.0.1",
        "--port",
        "29000",
        "--query",
    ]


def capped_sql(sql: str) -> str:
    statement = str(sql).strip()
    require(
        statement.startswith("SELECT ") and ";" not in statement,
        "canary SQL must be one source route SELECT",
    )
    require(
        statement.upper().count("SELECT ") == 1
        and re.search(r"\bFROM\s+markorbit_facts\.cn_case_current\s+FINAL\b", statement, re.I)
        is not None
        and re.search(r"\bLIMIT\s+2\s*$", statement, re.I) is not None,
        "canary must reuse the exact capped one-table FINAL route SQL",
    )
    settings = ",".join(
        f"{name}={value}" if type(value) is int else f"{name}='{value}'"
        for name, value in LIMITS.items()
    )
    return statement + " SETTINGS " + settings + " FORMAT JSONCompact"


def decode_typed(value: Any, column_type: str) -> Any:
    if value is None:
        return None
    if column_type.startswith("Nullable(") and column_type.endswith(")"):
        return decode_typed(value, column_type[9:-1])
    if column_type.startswith("Array(") and column_type.endswith(")"):
        require(isinstance(value, list), "JSONCompact Array type drift")
        return [decode_typed(item, column_type[6:-1]) for item in value]
    if re.fullmatch(r"U?Int(?:8|16|32|64|128|256)", column_type):
        require(
            type(value) in (int, str) and str(value).isdigit(), "JSONCompact integer encoding drift"
        )
        return int(value)
    return value


class FrozenReadClient:
    def __init__(self, backend: str) -> None:
        self.backend = backend
        self.last_read_statistics: dict[str, int] | None = None

    def query(self, sql: str, *, settings: dict[str, Any] | None = None) -> Any:
        require(
            settings is not None and settings.get("read_overflow_mode") == "throw",
            "source route must request bounded reads",
        )
        require(
            settings.get("max_rows_to_read", 0) >= LIMITS["max_rows_to_read"]
            and settings.get("max_bytes_to_read", 0) >= LIMITS["max_bytes_to_read"],
            "canary cap must not enlarge the route's accepted read budget",
        )
        data = json.loads(runner(cli(self.backend) + [capped_sql(sql)]))
        meta = data.get("meta")
        rows = data.get("data")
        statistics = data.get("statistics")
        require(
            isinstance(meta, list)
            and isinstance(rows, list)
            and isinstance(statistics, dict)
            and len(rows) <= 2,
            "JSONCompact result structure/row cap drift",
        )
        cols = [entry.get("name") for entry in meta]
        require(
            cols == list(EXPECTED_COLUMNS)
            and all(isinstance(entry.get("type"), str) for entry in meta),
            "source route projection/schema drift",
        )
        metrics = {name: int(statistics[name]) for name in ("rows_read", "bytes_read")}
        require(
            metrics["rows_read"] <= LIMITS["max_rows_to_read"]
            and metrics["bytes_read"] <= LIMITS["max_bytes_to_read"],
            "ClickHouse actual read budget exceeded",
        )
        parsed = []
        for row in rows:
            require(isinstance(row, list) and len(row) == len(meta), "JSONCompact row width drift")
            parsed.append(
                [decode_typed(value, entry["type"]) for value, entry in zip(row, meta, strict=True)]
            )
        self.last_read_statistics = metrics
        return SimpleNamespace(column_names=cols, result_rows=parsed)

    def insert(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("read-only canary forbids INSERT")

    def command(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("read-only canary forbids commands")


def load_and_preflight(repo: Path) -> tuple[dict[str, Any], Any]:
    require(sha(PLAN_PATH) == PLAN_SHA, "accepted merged-main canary plan SHA drift")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8-sig"))
    require(
        plan["status"] == "FROZEN_READ_ONLY_PLANS_NO_APPLY"
        and plan["read_canary"]["serving_route_activation_authorized"] is False
        and plan["merge_capacity"]["start_merges_or_optimize_authorized"] is False,
        "plan unexpectedly grants production Apply",
    )
    scope = plan["read_canary"]
    declared = scope["read_limits"]
    require(
        scope["probe_allowed_now"] is False
        and scope["source_target_query"] == "THE_SAME_BUILD_PAGE_SQL_AND_SETTINGS"
        and scope["limits"] == {"max_pages": 1, "max_results": 1, "max_output_rows": 2}
        and declared["read_overflow_mode"] == "throw"
        and all(
            LIMITS[name] <= declared[name]
            for name in (
                "max_threads",
                "max_rows_to_read",
                "max_bytes_to_read",
                "max_execution_time",
            )
        ),
        "canary scope, source SQL or resource limit drift",
    )
    code = {
        "app/cn/discovery_preliminary_publication.py": ROUTE_SHA,
        "app/cn/research_filing_to_prelim_duration.py": EPOCH_CODE_SHA,
        "scripts/freeze_cn_hot_c2b_next_plans.py": PLANNER_SHA,
    }
    for relative, expected in code.items():
        require(sha(repo / relative) == expected, "canary code SHA drift: " + relative)
    audit = runpy.run_path(
        str(repo / "scripts/audit_cn_hot_c2b_readiness.py"), run_name="readonly_canary_preflight"
    )
    fresh = audit["run_live"](repo, R9_EVIDENCE)
    require(
        fresh["status"] == "CUTOVER_NOT_AUTHORIZED"
        and fresh["six_accepted_total_rows"] == 1908623840
        and fresh["api_writers_stopped"]
        and fresh["source_and_target_idle"]
        and fresh["route_code_sha256"] == plan["fresh_readiness"]["route_code_sha256"],
        "live R9/route/currentness isolation drift",
    )
    candidate = fresh["routes"][plan["read_canary"]["route"]]
    require(
        candidate["dependencies"] == ["cn_case_current"]
        and not candidate["missing_target_tables"]
        and not candidate["serving_cutover_authorized"],
        "candidate route no longer source-only exact-table scope",
    )
    planner = runpy.run_path(
        str(repo / "scripts/freeze_cn_hot_c2b_next_plans.py"), run_name="readonly_canary_epoch"
    )
    control = planner["epoch"]()
    require(control == plan["control_epoch"], "CN serving epoch changed; refreeze")
    samples = plan["sample_proof"]
    require(
        len(samples) == 2
        and [s["key"] for s in samples] == plan["read_canary"]["sample_application_numbers"]
        and all(s["latest_source_equals_target_raw"] for s in samples),
        "frozen two-key source/target raw witness drift",
    )
    return plan, planner["epoch"]


def make_epoch_reader(frozen: dict[str, Any], read_control: Any) -> Any:
    from app.cn.research_filing_to_prelim_duration import ServingEpoch

    def current() -> ServingEpoch:
        actual = read_control()
        require(actual == frozen, "CN serving epoch changed across canary reads")
        return ServingEpoch(
            coverage_date=date.fromisoformat(frozen["coverage_date"]),
            max_success_sequence=frozen["max_success_sequence"],
            success_count=frozen["successful_packages"],
        )

    return current


def compare_exact_route(plan: dict[str, Any], read_control: Any) -> list[dict[str, Any]]:
    from app.cn.discovery_preliminary_publication import (
        PreliminaryPublicationDiscoveryRequest,
        execute_page,
    )
    from app.version import engine_version

    read_epoch = make_epoch_reader(plan["control_epoch"], read_control)
    result = []
    for case in plan["read_canary"]["sample_ranges"]:
        require(
            case["page_size"] == 1
            and case["cursor"] is None
            and case["end_exclusive"] == case["start_inclusive"] + "!",
            "unapproved bounded canary query range",
        )
        request = PreliminaryPublicationDiscoveryRequest(
            application_number_start=case["start_inclusive"],
            application_number_end=case["end_exclusive"],
            page_size=1,
            cursor=None,
        )
        source = FrozenReadClient("source")
        target = FrozenReadClient("target")
        before = read_epoch()
        a = execute_page(
            request, client=source, serving_epoch_getter=read_epoch, engine_version=engine_version()
        )
        b = execute_page(
            request, client=target, serving_epoch_getter=read_epoch, engine_version=engine_version()
        )
        require(read_epoch() == before, "serving epoch changed between backend reads")
        require(
            a == b
            and len(a["results"]) == 1
            and not a["next_cursor"]
            and a["results"][0]["application_number"] == case["start_inclusive"],
            "source FINAL vs target FINAL route parity not accepted",
        )
        require(
            source.last_read_statistics is not None and target.last_read_statistics is not None,
            "missing bounded ClickHouse read statistics",
        )
        raw = json.dumps(a, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        result.append(
            {
                "key": case["start_inclusive"],
                "result_count": len(a["results"]),
                "full_route_response_sha256": hashlib.sha256(raw).hexdigest(),
                "source_statistics": source.last_read_statistics,
                "target_statistics": target.last_read_statistics,
                "route_output_equal": True,
                "serving_epoch_equal": True,
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--authority-token", default="")
    args = parser.parse_args()
    require(
        args.preflight_only or args.authority_token == AUTHORITY,
        "exact reviewed read-only canary authority token required",
    )
    require(not RECEIPT.exists(), "canary receipt already exists; refuse retry")
    plan, read_control = load_and_preflight(args.repo)
    print("C2B_CANARY_PREFLIGHT_PASS plan_sha=" + PLAN_SHA, flush=True)
    if args.preflight_only:
        require(not args.authority_token, "preflight must not accept Apply token")
        print("C2B_CANARY_APPLY_PENDING_EXACT_AUTHORITY", flush=True)
        return
    # The token authorizes only bounded SELECT and a local evidence receipt.
    sys.path.insert(0, str(args.repo))
    facts = compare_exact_route(plan, read_control)
    require(
        len(facts) == 2 and all(item["route_output_equal"] for item in facts),
        "both exact-key route probes not accepted",
    )
    result = {
        "version": "CN_HOT_C2B_EXACT_READ_CANARY_R1",
        "status": "READ_ONLY_EXACT_KEY_PARITY_ACCEPTED_NOT_SERVING",
        "plan_sha256": PLAN_SHA,
        "operator_sha256": sha(Path(__file__)),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "epoch": plan["control_epoch"],
        "probes": facts,
        "route_activation_authorized": False,
        "api_started": False,
        "writer_started": False,
        "merge_started": False,
        "source_cleaned": False,
    }
    with RECEIPT.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
    print("C2B_CANARY_RECEIPT_SHA256=" + sha(RECEIPT), flush=True)
    print("C2B_READ_ONLY_PARITY_ACCEPTED_NOT_SERVING", flush=True)


if __name__ == "__main__":
    main()
