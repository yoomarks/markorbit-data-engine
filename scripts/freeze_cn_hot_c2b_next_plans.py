"""Freeze separate read-canary and bounded-merge *plans* for CN Hot C2b.

All ClickHouse reads use system metadata or exact-key, capped, non-FINAL SELECT.
Never start an API, change a serving endpoint, resume merges, or mutate data.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PRIOR_SHA = "b23bdf8441a20d9c222ca6b9dde288f146facd527b97604d948aac4a62ff04ab"
R9_SHA = "800dd39feecd6c130a3c8e59bc21703fcb00747f10f8db9a036fcf276fbe7da3"
ROUTE = "/api/v1/cn/discovery/preliminary-publications"
TABLE = "cn_case_current"
GIB = 1024**3
FLOOR_PERCENT = 30
BUFFER = 64 * GIB
MAX_INPUT = 16 * GIB
SAMPLE_KEYS = ("10002014", '"10002014"')
READ_LIMITS = {
    "max_threads": 1,
    "max_rows_to_read": 32768,
    "max_bytes_to_read": 32 * 1024 * 1024,
    "max_execution_time": 10,
    "read_overflow_mode": "throw",
}


def require(ok: bool, why: str) -> None:
    if not ok:
        raise RuntimeError(why)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv: list[str], timeout: int = 100) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    require(
        result.returncode == 0,
        argv[0] + " failed (rc=" + str(result.returncode) + "): " + result.stderr[-350:],
    )
    return result.stdout.strip()


def epoch() -> dict[str, Any]:
    sql = (
        "SELECT count(*) FILTER (WHERE status='PROCESSING'),"
        "count(*) FILTER (WHERE status='SUCCESS'),"
        "coalesce(max(package_sequence) FILTER (WHERE status='SUCCESS'),0),"
        "coalesce(max(coalesce(dataset_release_date,source_period_end)) "
        "FILTER (WHERE status='SUCCESS' AND package_kind='MONTHLY_PATCH')"
        "::text,'') FROM control.source_package WHERE jurisdiction='CN'"
    )
    base = [
        "docker.exe",
        "exec",
        "markorbit-data-engine-postgres-1",
        "psql",
        "-X",
        "-U",
        "markorbit",
        "-d",
        "markorbit_control",
        "-At",
    ]
    fields = run(base + ["-F", "|", "-c", sql]).split("|")
    require(len(fields) == 4, "CN serving epoch shape drift")
    count, success, sequence = (int(value) for value in fields[:3])
    coverage = fields[3]
    jobs = int(run(base + ["-c", "SELECT count(*) FROM control.job_run WHERE status='RUNNING'"]))
    require(
        count == 0
        and jobs == 0
        and success > 0
        and sequence > 0
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", coverage) is not None,
        "CN serving epoch is not quiescent/currently admitted",
    )
    return {
        "processing_packages": count,
        "successful_packages": success,
        "max_success_sequence": sequence,
        "coverage_date": coverage,
        "running_jobs": jobs,
        "watermark": f"{coverage}:{sequence}:{success}",
    }


def exact_key_sql(key: str) -> str:
    require(key in SAMPLE_KEYS, "unreviewed sample key")
    return "'10002014'" if key == "10002014" else "concat(unhex('22'),'10002014',unhex('22'))"


def sample_sql(key: str) -> str:
    limits = ",".join(
        f"{name}={value}" if isinstance(value, int) else f"{name}='{value}'"
        for name, value in READ_LIMITS.items()
    )
    sql = (
        "SELECT hex(application_number),toString(prelim_pub_date),"
        "is_deleted,source_rank,record_hash,source_row_hash,toString(case_id) "
        "FROM markorbit_facts.cn_case_current WHERE application_number = "
        + exact_key_sql(key)
        + " ORDER BY source_rank DESC LIMIT 3 SETTINGS "
        + limits
        + " FORMAT TSV"
    )
    require(
        sql.startswith("SELECT ") and " FINAL " not in sql and ";" not in sql,
        "sample must remain capped, single, non-FINAL SELECT",
    )
    return sql


def probe(key: str, *, target: bool) -> list[tuple[str, ...]]:
    prefix = (
        [
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
        if target
        else [
            "docker.exe",
            "exec",
            "markorbit-data-engine-clickhouse-1",
            "clickhouse-client",
            "--query",
        ]
    )
    lines = run(prefix + [sample_sql(key)], timeout=30).splitlines()
    rows = [tuple(line.split("\t")) for line in lines if line]
    require(
        0 < len(rows) < 3 and all(len(row) == 7 for row in rows),
        "exact-key sample is empty, over limit or malformed",
    )
    expected = key.encode("utf-8").hex().upper()
    for row in rows:
        require(
            row[0] == expected
            and row[2] in ("0", "1")
            and row[3].isdigit()
            and len(row[4]) == 64
            and len(row[5]) == 64
            and row[6],
            "sample is not the exact original application key/current facts",
        )
    return rows


def accepted_sample_proof() -> list[dict[str, Any]]:
    proof = []
    for key in SAMPLE_KEYS:
        source = probe(key, target=False)
        target = probe(key, target=True)
        require(
            len(target) == 1 and source[0] == target[0],
            "exact-key source latest/target raw parity failed",
        )
        require(
            target[0][2] == "0" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", target[0][1]) is not None,
            "sample not a visible case with a preliminary date",
        )
        proof.append(
            {
                "key": key,
                "raw_hex": key.encode().hex().upper(),
                "source_physical_versions_seen": len(source),
                "target_physical_versions_seen": len(target),
                "latest_source_equals_target_raw": True,
                "preliminary_publication_date": target[0][1],
                "has_literal_quote": key.startswith('"'),
                "is_route_final_parity": False,
            }
        )
    return proof


def build_plan(
    gate: dict[str, Any],
    control: dict[str, Any],
    samples: list[dict[str, Any]],
    prior_sha: str,
    auditor_sha: str,
) -> dict[str, Any]:
    require(
        prior_sha == PRIOR_SHA and gate["status"] == "CUTOVER_NOT_AUTHORIZED",
        "C2b accepted readiness gate drift",
    )
    require(
        gate["r9_final_receipt_sha256"] == R9_SHA
        and gate["api_binding"] == "SOURCE_CLICKHOUSE"
        and gate["api_writers_stopped"] is True
        and gate["source_and_target_idle"] is True
        and gate["six_accepted_total_rows"] == 1908623840,
        "R9, API isolation or CN target rows drift",
    )
    require(
        len(gate["routes"]) == 9 and len(gate["write_routes"]) == 3,
        "source/target route inventory drift",
    )
    route = gate["routes"][ROUTE]
    require(
        route["dependencies"] == [TABLE]
        and not route["missing_target_tables"]
        and route["serving_cutover_authorized"] is False,
        "preliminary-publication route no longer candidate-only",
    )
    require(
        all(not info["serving_cutover_authorized"] for info in gate["routes"].values())
        and all(not info["target_write_authorized"] for info in gate["write_routes"].values()),
        "unexpected source/target serving or writer authorization",
    )
    require(
        control["processing_packages"] == control["running_jobs"] == 0
        and control["max_success_sequence"] > 0
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", control["coverage_date"]),
        "unaccepted CN epoch",
    )
    require(
        len(samples) == 2
        and [s["key"] for s in samples] == list(SAMPLE_KEYS)
        and all(
            s["latest_source_equals_target_raw"] and not s["is_route_final_parity"] for s in samples
        ),
        "sample evidence drift",
    )
    storage = gate["capacity"]
    hot = storage["hot_cn"]
    floor = (hot["total"] * FLOOR_PERCENT + 99) // 100
    headroom = hot["free"] - floor
    require(
        headroom > BUFFER + 2 * GIB
        and all(storage[d]["free"] >= storage[d]["floor_30pct"] for d in ("D", "E")),
        "C2b physical/reserve envelope missing",
    )
    peak = min(2 * MAX_INPUT, headroom - BUFFER)
    cap = min(MAX_INPUT, peak // 2)
    require(peak > 0 and cap > 0, "bounded merge proposal has no scratch margin")
    expensive = storage["large_single_partitions"]
    require(
        set(expensive) == {"cn_goods_item_current", "cn_observed_event"}
        and all(v["partition"] == "tuple()" for v in expensive.values()),
        "large CN partition layout drift",
    )
    return {
        "version": "CN_HOT_C2B_NEXT_PLANS_V1",
        "issue": 843,
        "parent": 837,
        "status": "FROZEN_READ_ONLY_PLANS_NO_APPLY",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prior_readiness_sha256": PRIOR_SHA,
        "fresh_readiness_auditor_sha256": auditor_sha,
        "fresh_readiness": gate,
        "control_epoch": control,
        "sample_proof": samples,
        "read_canary": {
            "route": ROUTE,
            "source_table": TABLE,
            "serving_endpoint": "SOURCE_ONLY",
            "sample_application_numbers": list(SAMPLE_KEYS),
            "sample_ranges": [
                {"start_inclusive": key, "end_exclusive": key + "!", "page_size": 1, "cursor": None}
                for key in SAMPLE_KEYS
            ],
            "build_sql_from": "app.cn.discovery_preliminary_publication.build_page_sql",
            "source_target_query": "THE_SAME_BUILD_PAGE_SQL_AND_SETTINGS",
            "read_limits": READ_LIMITS,
            "limits": {"max_pages": 1, "max_results": 1, "max_output_rows": 2},
            "parity_required": [
                "source_FINAL_equals_target_FINAL_for_identical_range",
                "same_CN_serving_epoch_before_and_after_each_both_backend_read",
                "identical_candidates_and_source_references",
                "identical_page_query_snapshot_cursor_and_provenance",
                "raw_quoted_and_unquoted_keys_must_remain_distinct",
            ],
            "on_error": "NO_TARGET_SERVING_USE_SOURCE_ONLY_IF_EPOCH_UNCHANGED",
            "probe_allowed_now": False,
            "serving_route_activation_authorized": False,
        },
        "merge_capacity": {
            "target_hot_cn_total_bytes": hot["total"],
            "target_hot_cn_free_bytes": hot["free"],
            "filesystem_30pct_reserve_floor_bytes": floor,
            "free_above_30pct_floor_bytes": headroom,
            "extra_safety_buffer_bytes": BUFFER,
            "proposed_serial_peak_scratch_cap_bytes": peak,
            "proposed_input_parts_cap_bytes": cap,
            "proposed_concurrent_merges": 1,
            "single_partition_table_bytes": {
                name: expensive[name]["bytes"] for name in sorted(expensive)
            },
            "goods_whole_partition_final_admitted": False,
            "events_whole_partition_final_admitted": False,
            "require_exact_bounded_merge_operator": True,
            "start_merges_or_optimize_authorized": False,
            "source_reclaim_authorized": False,
        },
        "next_gate": "SEPARATE_EXACT_READ_CANARY_AND_BOUNDED_MERGE_APPLY_AUTHORITIES",
        "production_mutation_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    require(not options.output.exists(), "plan evidence already exists: no overwrite")
    require(sha(options.prior) == PRIOR_SHA, "accepted C2b readiness SHA changed")
    prior = json.loads(options.prior.read_text(encoding="utf-8-sig"))
    require(prior["status"] == "CUTOVER_NOT_AUTHORIZED", "prior C2b evidence was not fail-closed")
    auditor_path = options.repo / "scripts/audit_cn_hot_c2b_readiness.py"
    spec = importlib.util.spec_from_file_location("c2b_readonly_auditor", auditor_path)
    require(spec is not None and spec.loader is not None, "read-only C2b auditor import failed")
    auditor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auditor)
    before = epoch()
    fresh = auditor.run_live(options.repo, options.evidence_root)
    samples = accepted_sample_proof()
    after = epoch()
    require(before == after, "CN serving epoch changed during plan-only probes")
    plan = build_plan(fresh, after, samples, PRIOR_SHA, sha(auditor_path))
    payload = json.dumps(plan, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    with options.output.open("x", encoding="utf-8") as dest:
        dest.write(payload)
    print("C2B_NEXT_PLANS_SHA256=" + sha(options.output), flush=True)
    print("C2B_NEXT_PLANS_STATUS=" + plan["status"], flush=True)
    print("C2B_READ_CANARY_APPLY_AUTHORIZED=False", flush=True)
    print("C2B_MERGE_APPLY_AUTHORIZED=False", flush=True)


if __name__ == "__main__":
    main()
