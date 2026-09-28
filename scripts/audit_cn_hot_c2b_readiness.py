"""Fail-closed, metadata-only CN Hot C2b readiness gate (Issue #843).

No data-table SELECT/FINAL, DDL, DML, service lifecycle or merge operations.
Successful evidence is NOT serving, writer or compaction authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ISSUE = 843
R9_PLAN_SHA = "80447c33d867cc20d45652f0dc270d77830692af2d79ca7cad4c8b77864a229c"
R9_FINAL_SHA = "800dd39feecd6c130a3c8e59bc21703fcb00747f10f8db9a036fcf276fbe7da3"
R9_AUDITOR_SHA = "d29df99cfc87d6eafc3ef18de5a43631dc4059f891bc0becc8333c85574288cb"
DB = "markorbit_facts"
DEFAULT_EVIDENCE = Path(r"D:\yoomarks\governed-plans\837\phase-c2-hot-cn-migration")
# Explicit inventory. Anything missing or unproven stays on the source.
CASE = (
    "cn_case_current",
    "cn_case_scope_current",
    "cn_goods_item_current",
    "cn_goods_scope_lifecycle_current",
    "cn_case_party_current",
    "cn_observed_event",
    "cn_case_relation_current",
)
ROUTES: dict[str, tuple[str, ...]] = {
    "/api/cn/schema": ("*ALL_CN_TABLE_SCHEMAS*",),
    "/api/cn/summary": (
        "cn_case_current",
        "cn_case_scope_current",
        "cn_case_party_current",
        "cn_observed_event",
        "cn_case_relation_current",
        "cn_scope_carve_out_current",
        "cn_goods_item_current",
        "cn_goods_item_observation",
        "cn_goods_scope_lifecycle_current",
    ),
    "/api/cn/cases/{application_number}": CASE,
    "/api/v1/cn/cases/{application_number}": CASE,
    "/api/v1/cn/agents/by-name": (
        "schema_version",
        "cn_agent_name_candidate_lookup",
        "cn_agent_current",
    ),
    "/api/v1/cn/agents/{agent_code}": ("cn_agent_current",),
    "/api/v1/cn/entities/{entity_id}/trademarks": (
        "cn_entity_trademark_relationship_event",
        "cn_entity_trademark_portfolio_readiness",
        "cn_case_current",
    ),
    "/api/v1/cn/cases/{application_number}/relationships": (
        "schema_version",
        "cn_trademark_relationship_event",
    ),
    "/api/v1/cn/discovery/preliminary-publications": ("cn_case_current",),
}
WRITE_ROUTES = {
    "/api/admin/v2/fact-admissions/cn/citation-relations",
    "/api/admin/v2/fact-admissions/cn/trademark-gazette/chunks",
    "/api/admin/v2/fact-admissions/cn/trademark-gazette/finalize",
}
SIX = (
    "cn_goods_scope_lifecycle_current",
    "cn_case_current",
    "cn_case_scope_current",
    "cn_case_party_current",
    "cn_observed_event",
    "cn_goods_item_current",
)
EXPENSIVE = ("cn_goods_item_current", "cn_observed_event")


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def process(argv: list[str], *, timeout: int = 100) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    require(
        done.returncode == 0, "read-only command failed: " + argv[0] + ": " + done.stderr[-350:]
    )
    return done.stdout.strip()


def select(sql: str, *, target: bool) -> str:
    require(
        sql.startswith("SELECT ") and ";" not in sql and not re.search(r"\bFINAL\b", sql, re.I),
        "C2b operator permits metadata-only SELECT, never FINAL",
    )
    client = (
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
    return process(client + [sql])


def names(*, target: bool) -> set[str]:
    raw = select(
        "SELECT name FROM system.tables WHERE database='" + DB + "' FORMAT TSV", target=target
    )
    values = set(raw.splitlines())
    require(
        all(re.fullmatch(r"[a-z][a-z0-9_]*", value) for value in values),
        "unexpected table identifier",
    )
    return values


def part_rows() -> dict[str, dict[str, Any]]:
    wanted = ",".join("'" + name + "'" for name in SIX)
    raw = select(
        "SELECT table,count(),sum(rows),sum(bytes_on_disk),"
        "groupUniqArray(disk_name) FROM system.parts WHERE active AND "
        "database='" + DB + "' AND table IN (" + wanted + ") "
        "GROUP BY table ORDER BY table FORMAT TSV",
        target=True,
    )
    rows: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        table, count, nrows, size, disks = line.split("\t")
        require(
            table in SIX and table not in rows and disks == "['hot_cn']",
            "unexpected accepted target table/disk: " + table,
        )
        rows[table] = {
            "parts": int(count),
            "rows": int(nrows),
            "bytes": int(size),
            "disk": "hot_cn",
        }
    require(set(rows) == set(SIX), "target six-table physical coverage drift")
    return rows


def partition_rows() -> dict[str, dict[str, Any]]:
    wanted = ",".join("'" + name + "'" for name in EXPENSIVE)
    raw = select(
        "SELECT table,partition,count(),sum(bytes_on_disk),"
        "max(bytes_on_disk) FROM system.parts WHERE active AND "
        "database='" + DB + "' AND table IN (" + wanted + ") "
        "GROUP BY table,partition ORDER BY table,partition FORMAT TSV",
        target=True,
    )
    result: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        table, partition, count, total, largest = line.split("\t")
        require(
            table in EXPENSIVE and table not in result, "multiple/unexpected large CN partitions"
        )
        result[table] = {
            "partition": partition,
            "active_parts": int(count),
            "bytes": int(total),
            "largest_part_bytes": int(largest),
        }
    require(set(result) == set(EXPENSIVE), "large CN partitions missing")
    return result


def volume(letter: str) -> dict[str, int]:
    require(letter in ("D", "E"), "unexpected physical drive")
    ps = (
        "$v=Get-Volume -DriveLetter "
        + letter
        + "; @{total=[int64]$v.Size;free=[int64]$v.SizeRemaining;"
        "health=[string]$v.HealthStatus}|ConvertTo-Json -Compress"
    )
    value = json.loads(process(["powershell.exe", "-NoProfile", "-Command", ps]))
    require(
        value["health"] == "Healthy" and value["total"] > 0 and value["free"] >= 0,
        "physical volume unhealthy: " + letter,
    )
    return {
        "total": value["total"],
        "free": value["free"],
        "floor_30pct": (value["total"] * 30 + 99) // 100,
    }


def verify_route_inventory(repo: Path) -> dict[str, str]:
    paths = (repo / "app" / "main_core.py", repo / "app" / "integration_api.py")
    entries: set[str] = set()
    hashes: dict[str, str] = {}
    for path in paths:
        source = path.read_text(encoding="utf-8")
        hashes[str(path.relative_to(repo)).replace("\\", "/")] = digest(path)
        pattern = (
            r'@app\.get\("(/api/cn/[^"]+)"\)'
            if path.name == "main_core.py"
            else r'@router\.get\("(/cn/[^"]+)"\)'
        )
        entries.update(
            (route if route.startswith("/api/") else "/api/v1" + route)
            for route in re.findall(pattern, source)
        )
    require(
        entries == set(ROUTES),
        "CN API routes added/removed without "
        "a complete reviewed dependency inventory: " + repr(sorted(entries ^ set(ROUTES))),
    )
    admissions = (
        repo / "app/cn/citation_relation_admission_api.py",
        repo / "app/cn/trademark_gazette_admission_api.py",
    )
    writer_routes: set[str] = set()
    for path in admissions:
        source = path.read_text(encoding="utf-8")
        require(
            'prefix="/api/admin/v2/fact-admissions"' in source, "CN admission route prefix drift"
        )
        writer_routes.update(
            "/api/admin/v2/fact-admissions" + route
            for route in re.findall(r'@router\.post\("(/cn/[^"]+)"\)', source)
        )
        hashes[str(path.relative_to(repo)).replace("\\", "/")] = digest(path)
    require(
        writer_routes == WRITE_ROUTES,
        "CN writer route inventory drift: " + repr(sorted(writer_routes ^ WRITE_ROUTES)),
    )
    core = paths[0].read_text(encoding="utf-8")
    start = core.index("def cn_case(application_number:")
    end = core.index("\n\n@app.get(", start)
    for table in CASE:
        require(
            "markorbit_facts." + table in core[start:end],
            "CN case query dependency drift: " + table,
        )
    lookup = repo / "app/cn/agent_name_lookup.py"
    text = lookup.read_text(encoding="utf-8")
    for table in ROUTES["/api/v1/cn/agents/by-name"]:
        require(
            "markorbit_facts." + table in text
            or (table == "cn_agent_name_candidate_lookup" and "CN_AGENT_NAME_LOOKUP_TABLE" in text),
            "agent-name dependency drift: " + table,
        )
    hashes["app/cn/agent_name_lookup.py"] = digest(lookup)
    return hashes


def route_decisions(source: set[str], target: set[str]) -> dict[str, Any]:
    output = {}
    for route, deps in ROUTES.items():
        required = (
            sorted(name for name in source if name.startswith("cn_"))
            if deps == ("*ALL_CN_TABLE_SCHEMAS*",)
            else list(deps)
        )
        missing_source = sorted(set(required) - source)
        require(
            not missing_source,
            "route source dependencies missing: " + route + " " + repr(missing_source),
        )
        absent = sorted(set(required) - target)
        output[route] = {
            "dependencies": required,
            "missing_target_tables": absent,
            "decision": (
                "SOURCE_ONLY_MISSING_TARGET_DEPENDENCIES"
                if absent
                else "SOURCE_ONLY_PENDING_EPOCH_AND_BOUNDED_READ_PROOF"
            ),
            "serving_cutover_authorized": False,
        }
    return output


def make_report(
    *,
    plan: dict[str, Any],
    final: dict[str, Any],
    source: set[str],
    target: set[str],
    physical: dict[str, Any],
    partitions: dict[str, Any],
    disks: dict[str, Any],
    source_idle: bool,
    target_idle: bool,
    api_source_binding: bool,
    code_hashes: dict[str, str],
) -> dict[str, Any]:
    require(
        plan["version"] == "HOT_CN_NULL_SAFE_TWO_TABLE_MIGRATION_PLAN_V9"
        and plan["main_sha"] == "3ebfb23fbe4fc6fae76c1bc6dd5fca264b87a280",
        "frozen R9 plan identity drift",
    )
    require(
        final["status"] == "PASS"
        and final["r9_plan_sha256"] == R9_PLAN_SHA
        and final["source_retained"] is True
        and final["source_cleanup_performed"] is False
        and final["api_writers_stopped"] is True,
        "R9 final safety/identity drift",
    )
    require(
        source_idle and target_idle and api_source_binding,
        "current source/target isolation or source API binding drift",
    )
    require(set(SIX) <= source and set(SIX) <= target, "migrated six table presence drift")
    expected = {item["name"]: int(item["source_final_rows"]) for item in plan["accepted_tables"]}
    for entry in final["completed_remaining_tables"]:
        # These values are checked against SHA-accepted unit receipts in run_live.
        expected[entry["table"]] = int(entry["accepted_rows"])
    require(set(expected) == set(SIX), "aggregate R9 row contract drift")
    for name, rows in expected.items():
        require(physical[name]["rows"] == rows, "target accepted rows drift: " + name)
    require(
        sum(item["rows"] for item in physical.values()) == final["all_six_target_raw_current_rows"],
        "six-table aggregate physical row drift",
    )
    routes = route_decisions(source, target)
    hot = disks["hot_cn"]
    require(hot["free"] > 0 and hot["total"] > hot["free"], "hot_cn capacity invalid")
    for letter in ("D", "E"):
        require(
            disks[letter]["free"] >= disks[letter]["floor_30pct"],
            "host physical reserve below 30%: " + letter,
        )
    return {
        "version": "CN_HOT_C2B_READONLY_READINESS_V1",
        "issue": ISSUE,
        "parent_issue": 837,
        "status": "CUTOVER_NOT_AUTHORIZED",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "r9_plan_sha256": R9_PLAN_SHA,
        "r9_final_receipt_sha256": R9_FINAL_SHA,
        "source_table_count": len(source),
        "target_table_count": len(target),
        "api_binding": "SOURCE_CLICKHOUSE",
        "api_writers_stopped": True,
        "source_and_target_idle": True,
        "six_accepted_table_physical": physical,
        "six_accepted_total_rows": final["all_six_target_raw_current_rows"],
        "routes": routes,
        "route_code_sha256": code_hashes,
        "write_routes": {
            route: {"decision": "SOURCE_ONLY_WRITE_FROZEN", "target_write_authorized": False}
            for route in sorted(WRITE_ROUTES)
        },
        "capacity": {
            "hot_cn": hot,
            "D": disks["D"],
            "E": disks["E"],
            "large_single_partitions": partitions,
        },
        "global_api_endpoint_cutover_authorized": False,
        "target_final_query_performed": False,
        "source_data_mutated": False,
        "api_started": False,
        "merges_restarted": False,
        "compaction_authorized": False,
        "source_cleanup_authorized": False,
        "next_gate": "FREEZE_EXACT_PER_ROUTE_READ_CANARY_AND_BOUNDED_MERGE_PLANS",
    }


def run_live(repo: Path, evidence: Path) -> dict[str, Any]:
    plan_path = evidence / "hot-cn-null-safe-two-table-migration-plan-r9.json"
    final_path = evidence / "hot-cn-null-safe-two-table-migration-final-receipt-r9.json"
    auditor = evidence / "audit-hot-cn-r9-readonly.py"
    require(
        digest(plan_path) == R9_PLAN_SHA
        and digest(final_path) == R9_FINAL_SHA
        and digest(auditor) == R9_AUDITOR_SHA,
        "frozen R9 evidence SHA drift",
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    final = json.loads(final_path.read_text(encoding="utf-8-sig"))
    audit = json.loads(process([sys.executable, "-B", str(auditor)]))
    require(
        audit["status"] == "RECEIPT_AND_RESIDENCY_ACCEPTED", "independent R9 auditor not accepted"
    )
    for item in final["completed_remaining_tables"]:
        path = Path(item["receipt_path"])
        require(digest(path) == item["receipt_sha256"], "new table-unit receipt SHA drift")
        unit = json.loads(path.read_text(encoding="utf-8-sig"))
        require(
            unit["status"] == "PASS"
            and unit["source_final_fingerprint"] == unit["target_raw_fingerprint"],
            "source FINAL / target raw fingerprint drift",
        )
        item["accepted_rows"] = int(unit["source_final_fingerprint"].split("\t")[0])
    code_hashes = verify_route_inventory(repo)
    compose = (repo / "docker-compose.yml").read_text(encoding="utf-8")
    binds = re.findall(r"^\s+CLICKHOUSE_HOST:\s+([^\s#]+)", compose, re.M)
    source_binding = len(binds) >= 2 and all(v == "clickhouse" for v in binds)
    a = names(target=False)
    b = names(target=True)
    phys = part_rows()
    partitions = partition_rows()
    idle_sql = (
        "SELECT count() FROM system.merges WHERE database='"
        + DB
        + "' AND table IN ("
        + ",".join("'" + n + "'" for n in SIX)
        + ") FORMAT TSV"
    )
    source_idle = select(idle_sql, target=False) == "0"
    target_idle = select(idle_sql, target=True) == "0"
    for n in ("markorbit-data-engine-api-1", "markorbit-data-engine-api-dogfood-1169"):
        require(
            process(["docker.exe", "inspect", n, "--format", "{{.State.Running}}"]) == "false",
            "API writer unexpectedly running: " + n,
        )
    disk_raw = select(
        "SELECT total_space,free_space FROM system.disks WHERE name='hot_cn' FORMAT TSV",
        target=True,
    ).split("\t")
    require(len(disk_raw) == 2, "hot_cn disk missing")
    disks = {
        "hot_cn": {"total": int(disk_raw[0]), "free": int(disk_raw[1])},
        "D": volume("D"),
        "E": volume("E"),
    }
    return make_report(
        plan=plan,
        final=final,
        source=a,
        target=b,
        physical=phys,
        partitions=partitions,
        disks=disks,
        source_idle=source_idle,
        target_idle=target_idle,
        api_source_binding=source_binding,
        code_hashes=code_hashes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    require(
        args.output is None or not args.output.exists(),
        "existing evidence file must never be overwritten",
    )
    result = run_live(args.repo, args.evidence_root)
    serialized = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as f:
            f.write(serialized)
        print("C2B_EVIDENCE_SHA256=" + digest(args.output), flush=True)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
