from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.cn.agent_exact_read import read_agent_exact
from app.cn.agent_name_lookup import (
    AGENT_NAME_LOOKUP_READY_VERSION,
    CN_AGENT_NAME_LOOKUP_TABLE,
    agents_by_name,
)
from app.db import clickhouse_client
from app.read_performance_baseline import DEFAULT_QUERY_BUDGET


RECEIPT_VERSION = "CN_AGENT_NAME_LOOKUP_PRODUCTION_ACCEPTANCE_RECEIPT_V1"
SOURCE_TABLE = "markorbit_facts.cn_agent_current"
READY_COMPONENT = "CN_AGENT_NAME_LOOKUP"
BENCHMARK_RUNS = 7
SLO_P95_MS = 300.0
DEFAULT_HTTP_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_API_KEY_ENV = "MARKORBIT_INTEGRATION_API_KEY"
_HEX = frozenset("0123456789abcdef")
READ_SETTINGS = {**DEFAULT_QUERY_BUDGET, "max_threads": 1}

SOURCE_BINDING_HASH = (
    "cityHash64(concat(agent_name_norm, '\\x1f', agent_code, '\\x1f', "
    "toString(source_row_hash), '\\x1f', toString(source_rank)))"
)
LOOKUP_BINDING_HASH = (
    "cityHash64(concat(normalized_name, '\\x1f', agent_code, '\\x1f', "
    "toString(source_row_hash), '\\x1f', toString(source_rank)))"
)


@dataclass(frozen=True, slots=True)
class ProjectionStats:
    rows: int
    distinct_agent_codes: int
    max_source_rank: int
    max_ingested_at: str
    binding_sum: str
    binding_xor: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=_repo_root(),
        text=True,
        encoding="utf-8",
    ).strip()


def exact_main_sha() -> str:
    head = _git("rev-parse", "HEAD").lower()
    origin = _git("rev-parse", "origin/main").lower()
    dirty = _git("status", "--porcelain=v1")
    if head != origin:
        raise RuntimeError("production acceptance requires HEAD == origin/main")
    if dirty:
        raise RuntimeError("production acceptance requires a clean exact-main worktree")
    if len(head) != 40 or any(ch not in _HEX for ch in head):
        raise RuntimeError("production acceptance could not resolve exact main SHA")
    return head


def _rows(client: Any, sql: str) -> list[list[Any]]:
    result = client.query(sql, settings=READ_SETTINGS)
    return [list(row) for row in result.result_rows]


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        observed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        observed = datetime.fromisoformat(text)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return (
        observed.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _source_stats(client: Any) -> ProjectionStats:
    rows = _rows(
        client,
        f"""
        SELECT
            countIf(is_deleted=0 AND agent_name_norm!=''),
            uniqExactIf(agent_code, is_deleted=0 AND agent_name_norm!=''),
            maxIf(source_rank, is_deleted=0 AND agent_name_norm!=''),
            maxIf(ingested_at, is_deleted=0 AND agent_name_norm!=''),
            toString(sumIf({SOURCE_BINDING_HASH}, is_deleted=0 AND agent_name_norm!='')),
            toString(groupBitXorIf({SOURCE_BINDING_HASH}, is_deleted=0 AND agent_name_norm!=''))
        FROM {SOURCE_TABLE} FINAL
        """,
    )
    if len(rows) != 1:
        raise RuntimeError("CN current Agent stats are not exact-one")
    row = rows[0]
    return ProjectionStats(
        rows=int(row[0]),
        distinct_agent_codes=int(row[1]),
        max_source_rank=int(row[2]),
        max_ingested_at=_iso(row[3]),
        binding_sum=str(row[4]),
        binding_xor=str(row[5]),
    )


def _lookup_stats(client: Any) -> ProjectionStats:
    rows = _rows(
        client,
        f"""
        SELECT
            countIf(is_deleted=0),
            uniqExactIf(agent_code, is_deleted=0),
            maxIf(source_rank, is_deleted=0),
            maxIf(ingested_at, is_deleted=0),
            toString(sumIf({LOOKUP_BINDING_HASH}, is_deleted=0)),
            toString(groupBitXorIf({LOOKUP_BINDING_HASH}, is_deleted=0))
        FROM {CN_AGENT_NAME_LOOKUP_TABLE} FINAL
        """,
    )
    if len(rows) != 1:
        raise RuntimeError("CN Agent-name lookup stats are not exact-one")
    row = rows[0]
    return ProjectionStats(
        rows=int(row[0]),
        distinct_agent_codes=int(row[1]),
        max_source_rank=int(row[2]),
        max_ingested_at=_iso(row[3]),
        binding_sum=str(row[4]),
        binding_xor=str(row[5]),
    )


def projection_audit(client: Any) -> dict[str, object]:
    source = _source_stats(client)
    lookup = _lookup_stats(client)
    source_dict = asdict(source)
    lookup_dict = asdict(lookup)
    checks = {
        "row_count_match": source.rows == lookup.rows,
        "distinct_agent_code_match": (
            source.distinct_agent_codes == lookup.distinct_agent_codes
        ),
        "max_source_rank_match": source.max_source_rank == lookup.max_source_rank,
        "max_ingested_at_match": source.max_ingested_at == lookup.max_ingested_at,
        "binding_sum_match": source.binding_sum == lookup.binding_sum,
        "binding_xor_match": source.binding_xor == lookup.binding_xor,
    }
    return {
        "source": source_dict,
        "lookup": lookup_dict,
        "checks": checks,
        "complete": all(checks.values()) and source.rows > 0,
    }


def ready_provenance(client: Any) -> dict[str, object]:
    rows = _rows(
        client,
        f"""
        SELECT version, toString(applied_at)
        FROM markorbit_facts.schema_version FINAL
        WHERE component='{READY_COMPONENT}'
        LIMIT 2
        """,
    )
    if len(rows) != 1:
        raise RuntimeError("CN Agent-name READY marker is not exact-one")
    version = str(rows[0][0])
    if version != AGENT_NAME_LOOKUP_READY_VERSION:
        raise RuntimeError(
            f"unexpected CN Agent-name READY version: {version or '<empty>'}"
        )
    return {
        "component": READY_COMPONENT,
        "version": version,
        "applied_at": _iso(rows[0][1]),
    }


def sample_name(client: Any) -> dict[str, str]:
    rows = _rows(
        client,
        f"""
        SELECT normalized_name, any(agent_code)
        FROM {CN_AGENT_NAME_LOOKUP_TABLE} FINAL
        WHERE is_deleted=0 AND normalized_name!=''
        GROUP BY normalized_name
        HAVING count() BETWEEN 1 AND 20
        ORDER BY cityHash64(normalized_name)
        LIMIT 1
        """,
    )
    if len(rows) != 1:
        raise RuntimeError("could not select a bounded CN Agent-name benchmark sample")
    return {
        "normalized_name": str(rows[0][0]),
        "agent_code": str(rows[0][1]),
    }


def _p95(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("benchmark values cannot be empty")
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def benchmark_lookup(
    client: Any,
    normalized_name: str,
    *,
    runs: int = BENCHMARK_RUNS,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, object]:
    if runs != BENCHMARK_RUNS:
        raise ValueError(f"production acceptance requires exactly {BENCHMARK_RUNS} runs")
    elapsed_ms: list[float] = []
    candidate_counts: list[int] = []
    match_counts: list[int] = []
    semantics: set[str] = set()
    for _ in range(runs):
        started = clock()
        result = agents_by_name(client, normalized_name)
        elapsed_ms.append(round((clock() - started) * 1000.0, 3))
        candidate_counts.append(int(result["candidate_count"]))
        match_counts.append(int(result["match_count"]))
        semantics.add(str(result["semantics"]))
    p95 = _p95(elapsed_ms)
    expected_semantics = "CURRENT_OFFICIAL_CNIPA_AGENT_NAME_FACTS_NO_IDENTITY_RESOLUTION"
    passed = (
        p95 <= SLO_P95_MS
        and len(set(candidate_counts)) == 1
        and len(set(match_counts)) == 1
        and match_counts[0] >= 1
        and semantics == {expected_semantics}
    )
    return {
        "runs": runs,
        "normalized_name": normalized_name,
        "elapsed_ms": elapsed_ms,
        "p50_ms": round(statistics.median(elapsed_ms), 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(elapsed_ms), 3),
        "slo_p95_ms": SLO_P95_MS,
        "candidate_count": candidate_counts[0],
        "match_count": match_counts[0],
        "stable_candidate_count": len(set(candidate_counts)) == 1,
        "stable_match_count": len(set(match_counts)) == 1,
        "semantics": expected_semantics,
        "passed": passed,
    }


def exact_agent_currentness(
    client: Any, agent_code: str, normalized_name: str
) -> dict[str, object]:
    result = read_agent_exact(client, agent_code)
    record = result.get("record")
    if not isinstance(record, Mapping):
        raise RuntimeError("benchmark Agent exact read returned no current record")
    if str(record.get("agent_name_norm") or "") != normalized_name:
        raise RuntimeError("benchmark Agent exact read no longer matches sampled name")
    if result.get("semantics") != (
        "CURRENT_OFFICIAL_CNIPA_AGENT_CODE_FACT_NO_IDENTITY_RESOLUTION"
    ):
        raise RuntimeError("benchmark Agent exact-read semantics drifted")
    reference = dict(record.get("source_reference") or {})
    currentness = dict(record.get("currentness") or {})
    if reference.get("owner") != "DATA_ENGINE" or reference.get("kind") != "CN_AGENT":
        raise RuntimeError("benchmark Agent source reference drifted")
    if currentness.get("state") != "CURRENT_SOURCE_FACT":
        raise RuntimeError("benchmark Agent currentness is not current")
    return {
        "agent_code": str(record["agent_code"]),
        "normalized_name": normalized_name,
        "source_reference": reference,
        "currentness": currentness,
        "legal_identity_verified": bool(record.get("legal_identity_verified")),
        "customer_relationship_established": bool(
            record.get("customer_relationship_established")
        ),
        "professional_appointment_established": bool(
            record.get("professional_appointment_established")
        ),
        "semantics": result["semantics"],
    }


def http_smoke(
    base_url: str,
    normalized_name: str,
    *,
    api_key: str | None,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    query = urlencode({"name": normalized_name})
    url = f"{base_url.rstrip('/')}/api/v1/cn/agents/by-name?{query}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout_seconds) as response:
        status_code = int(response.status)
        payload = json.loads(response.read().decode("utf-8"))
    if status_code != 200:
        raise RuntimeError(f"CN Agent-name HTTP smoke returned {status_code}")
    owner_payload = payload.get("payload") if isinstance(payload, dict) else None
    if not isinstance(owner_payload, dict):
        raise RuntimeError("CN Agent-name HTTP smoke returned an invalid envelope")
    if int(owner_payload.get("match_count") or 0) < 1:
        raise RuntimeError("CN Agent-name HTTP smoke returned no current fact")
    expected = "CURRENT_OFFICIAL_CNIPA_AGENT_NAME_FACTS_NO_IDENTITY_RESOLUTION"
    if owner_payload.get("semantics") != expected:
        raise RuntimeError("CN Agent-name HTTP smoke semantics drifted")
    return {
        "status_code": status_code,
        "resource_kind": payload.get("resource_kind"),
        "fact_state": payload.get("fact_state"),
        "normalized_name": owner_payload.get("normalized_name"),
        "candidate_count": int(owner_payload.get("candidate_count") or 0),
        "match_count": int(owner_payload.get("match_count") or 0),
        "semantics": owner_payload.get("semantics"),
        "auth_header_sent": bool(api_key),
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_receipt(path: Path, payload: Mapping[str, object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return _sha256_file(path)


def run_acceptance(
    *,
    receipt_path: Path,
    base_url: str = DEFAULT_HTTP_BASE_URL,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = exact_main_sha,
    http_runner: Callable[..., dict[str, object]] = http_smoke,
) -> dict[str, object]:
    target = client or clickhouse_client()
    main_sha = main_sha_getter()

    audit = projection_audit(target)
    if audit["complete"] is not True:
        raise RuntimeError("CN Agent-name source/projection completeness verification failed")

    ready = ready_provenance(target)
    sample = sample_name(target)
    benchmark = benchmark_lookup(target, sample["normalized_name"])
    if benchmark["passed"] is not True:
        raise RuntimeError("CN Agent-name production benchmark failed")

    exact = exact_agent_currentness(
        target,
        str(sample["agent_code"]),
        str(sample["normalized_name"]),
    )
    api_key = os.environ.get(api_key_env, "").strip() or None
    if api_key and "," in api_key:
        raise RuntimeError(
            f"{api_key_env} must contain one bearer key, not a rotation list"
        )
    smoke = http_runner(
        base_url,
        str(sample["normalized_name"]),
        api_key=api_key,
    )

    receipt = {
        "version": RECEIPT_VERSION,
        "status": "SUCCESS",
        "issue": 768,
        "implementation_sha": main_sha,
        "mutation_performed": False,
        "projection_audit": audit,
        "ready_provenance": ready,
        "benchmark": benchmark,
        "exact_agent_currentness": exact,
        "http_smoke": smoke,
        "no_identity_resolution_claimed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    sha = _write_receipt(receipt_path, receipt)
    return {
        **receipt,
        "receipt_path": str(receipt_path),
        "receipt_sha256": sha,
    }


def _default_receipt() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _repo_root() / "reports" / f"cn_agent_name_lookup_acceptance_{stamp}.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only production acceptance for CN Agent-name serving"
    )
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--base-url", default=DEFAULT_HTTP_BASE_URL)
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    args = parser.parse_args(argv)

    result = run_acceptance(
        receipt_path=args.receipt or _default_receipt(),
        base_url=args.base_url,
        api_key_env=args.api_key_env,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
