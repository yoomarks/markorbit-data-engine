from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from app.cn.applicant_name_lookup_backfill_control import (
    CNApplicantServingEpoch,
    current_cn_applicant_serving_epoch,
)
from app.cn.entity_trademark_portfolio import READY_VERSION
from app.cn.relationship_timeline import RELATIONSHIP_TIMELINE_READY_VERSION
from app.db import clickhouse_client

PLAN_VERSION = "CN_ENTITY_PORTFOLIO_ACTIVATION_PLAN_V2"
PROGRESS_VERSION = "CN_ENTITY_PORTFOLIO_ACTIVATION_PROGRESS_V1"
RECEIPT_VERSION = "CN_ENTITY_PORTFOLIO_ACTIVATION_RECEIPT_V1"
RELATIONSHIP_TABLE = "markorbit_facts.cn_trademark_relationship_event"
ENTITY_TABLE = "markorbit_facts.cn_entity_trademark_relationship_event"
READINESS_TABLE = "markorbit_facts.cn_entity_trademark_portfolio_readiness"
SOURCE_TABLE = "markorbit_facts.cn_observed_event"
RELATIONSHIP_EVENT_TYPES = (
    "OWNER_RELATION_OBSERVED",
    "OWNER_RELATION_SUPERSEDED_OBSERVED",
    "CO_OWNER_RELATION_OBSERVED",
    "CO_OWNER_RELATION_SUPERSEDED_OBSERVED",
    "AGENT_RELATION_OBSERVED",
    "AGENT_RELATION_SUPERSEDED_OBSERVED",
)
EXPECTED_RELATIONSHIP_SORTING_KEY = "application_number, event_hash"
EXPECTED_ENTITY_SORTING_KEY = "entity_id, role, application_number, relation_key, event_hash"
DEFAULT_RELATIONSHIP_BATCH_EVENTS = 250_000
DEFAULT_ENTITY_BATCH_APPLICATIONS = 5_000
MAX_RELATIONSHIP_BATCH_EVENTS = 1_000_000
MAX_ENTITY_BATCH_APPLICATIONS = 20_000
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ActivationProgress:
    plan_sha256: str
    stage: str = "RELATIONSHIP"
    after_event_hash: str = ""
    after_application_number: str = ""
    relationship_rows_verified: int = 0
    entity_rows_verified: int = 0
    entity_batches_verified: int = 0

    def __post_init__(self) -> None:
        if self.stage not in {"RELATIONSHIP", "ENTITY", "COMPLETE"}:
            raise ValueError("activation stage is invalid")
        if not HEX64.fullmatch(self.plan_sha256):
            raise ValueError("progress plan SHA-256 is invalid")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8").rstrip("\x00")
    return str(value or "")


def _sorting_key(value: str) -> str:
    return value.replace("`", "").replace("(", "").replace(")", "").strip()


def current_main_sha(repo_root: Path | None = None) -> str:
    root = repo_root or Path(__file__).resolve().parents[2]
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8"
    ).strip().lower()
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=root, text=True, encoding="utf-8"
    ).strip()
    if branch != "main" or not HEX40.fullmatch(sha):
        raise RuntimeError("CN entity activation must run from exact local main")
    return sha


def _event_type_sql() -> str:
    return ", ".join("'" + item + "'" for item in RELATIONSHIP_EVENT_TYPES)


def _query_rows(client: Any, sql: str, *, settings: Mapping[str, Any] | None = None) -> list[tuple[Any, ...]]:
    result = client.query(sql, settings=dict(settings or {"max_threads": 1}))
    return list(result.result_rows)


def target_schema_state(client: Any) -> dict[str, Any]:
    rows = _query_rows(
        client,
        """
        SELECT name, engine, sorting_key
        FROM system.tables
        WHERE database = 'markorbit_facts'
          AND name IN (
            'cn_observed_event',
            'cn_trademark_relationship_event',
            'cn_trademark_relationship_event_mv',
            'cn_entity_trademark_relationship_event',
            'cn_entity_trademark_relationship_event_mv',
            'cn_entity_trademark_portfolio_readiness'
          )
        ORDER BY name
        """,
    )
    tables = {
        str(name): {"engine": str(engine), "sorting_key": _sorting_key(str(sorting_key))}
        for name, engine, sorting_key in rows
    }
    expected = {
        "cn_observed_event",
        "cn_trademark_relationship_event",
        "cn_trademark_relationship_event_mv",
        "cn_entity_trademark_relationship_event",
        "cn_entity_trademark_relationship_event_mv",
        "cn_entity_trademark_portfolio_readiness",
    }
    if set(tables) != expected:
        raise RuntimeError("CN entity activation target schema is incomplete")
    if tables["cn_trademark_relationship_event"]["sorting_key"] != EXPECTED_RELATIONSHIP_SORTING_KEY:
        raise RuntimeError("CN relationship timeline sorting key drifted")
    if tables["cn_entity_trademark_relationship_event"]["sorting_key"] != EXPECTED_ENTITY_SORTING_KEY:
        raise RuntimeError("CN entity portfolio sorting key drifted")
    if tables["cn_trademark_relationship_event_mv"]["engine"] != "MaterializedView":
        raise RuntimeError("CN relationship timeline incremental projection is missing")
    if tables["cn_entity_trademark_relationship_event_mv"]["engine"] != "MaterializedView":
        raise RuntimeError("CN entity portfolio incremental projection is missing")
    return tables


def relationship_source_stats(
    client: Any, *, max_source_rank: int | None = None
) -> dict[str, Any]:
    rank_clause = (
        f"AND source_rank <= {int(max_source_rank)}"
        if max_source_rank is not None
        else ""
    )
    rows = _query_rows(
        client,
        f"""
        SELECT
            count() AS row_count,
            max(source_rank) AS max_source_rank,
            max(toString(event_hash)) AS max_event_hash,
            max(application_number) AS max_application_number,
            groupBitXor(cityHash64(event_hash)) AS event_hash_xor
        FROM {SOURCE_TABLE} FINAL
        WHERE event_type IN ({_event_type_sql()})
          {rank_clause}
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError("CN relationship source statistics are unavailable")
    row = rows[0]
    stats = {
        "row_count": int(row[0] or 0),
        "max_source_rank": int(row[1] or 0),
        "max_event_hash": _text(row[2]),
        "max_application_number": _text(row[3]),
        "event_hash_xor": int(row[4] or 0),
    }
    if stats["row_count"] <= 0 or stats["max_source_rank"] <= 0:
        raise RuntimeError("CN relationship source statistics are empty")
    if len(stats["max_event_hash"]) != 64 or not stats["max_application_number"]:
        raise RuntimeError("CN relationship source watermark is malformed")
    return stats


def _validate_batch_sizes(relationship_batch_events: int, entity_batch_applications: int) -> None:
    if not 1 <= relationship_batch_events <= MAX_RELATIONSHIP_BATCH_EVENTS:
        raise ValueError("relationship batch size is outside accepted bounds")
    if not 1 <= entity_batch_applications <= MAX_ENTITY_BATCH_APPLICATIONS:
        raise ValueError("entity application batch size is outside accepted bounds")


def prepare_activation_plan(
    output_path: Path,
    *,
    relationship_batch_events: int = DEFAULT_RELATIONSHIP_BATCH_EVENTS,
    entity_batch_applications: int = DEFAULT_ENTITY_BATCH_APPLICATIONS,
    client: Any | None = None,
    epoch_getter: Callable[[], CNApplicantServingEpoch] = current_cn_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, Any]:
    _validate_batch_sizes(relationship_batch_events, entity_batch_applications)
    target = client or clickhouse_client()
    implementation_sha = main_sha_getter().lower()
    if not HEX40.fullmatch(implementation_sha):
        raise RuntimeError("activation plan requires an exact implementation SHA")
    plan = {
        "version": PLAN_VERSION,
        "expected_main": implementation_sha,
        "implementation_sha": implementation_sha,
        "source_epoch": epoch_getter().to_dict(),
        "source_stats": relationship_source_stats(target),
        "target_schema": target_schema_state(target),
        "relationship_batch_events": relationship_batch_events,
        "entity_batch_applications": entity_batch_applications,
        "mutation_scope": {
            "insert_only_tables": [RELATIONSHIP_TABLE, ENTITY_TABLE, READINESS_TABLE],
            "readiness_marker": RELATIONSHIP_TIMELINE_READY_VERSION,
            "destructive_operations": False,
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return envelope


def load_activation_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    expected_sha = str(expected_sha).strip().lower()
    if not HEX64.fullmatch(expected_sha):
        raise ValueError("plan SHA must be an exact lowercase SHA-256")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"plan", "plan_sha256"} or not isinstance(envelope["plan"], dict):
        raise RuntimeError("activation plan envelope is malformed")
    computed = _sha256(envelope["plan"])
    if envelope["plan_sha256"] != computed or computed != expected_sha:
        raise RuntimeError("activation plan SHA-256 mismatch")
    plan = dict(envelope["plan"])
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("activation plan version is unsupported")
    return plan


def validate_live_plan(
    plan: Mapping[str, Any],
    *,
    client: Any,
    epoch_getter: Callable[[], CNApplicantServingEpoch],
    main_sha_getter: Callable[[], str],
) -> CNApplicantServingEpoch:
    sha = main_sha_getter().lower()
    if sha != plan.get("expected_main") or sha != plan.get("implementation_sha"):
        raise RuntimeError("activation implementation SHA drifted from plan")
    epoch = epoch_getter()
    if target_schema_state(client) != dict(plan.get("target_schema") or {}):
        raise RuntimeError("CN activation target schema drifted from plan")
    frozen_stats = dict(plan.get("source_stats") or {})
    max_source_rank = int(frozen_stats.get("max_source_rank") or 0)
    if relationship_source_stats(client, max_source_rank=max_source_rank) != frozen_stats:
        raise RuntimeError("CN frozen relationship source snapshot drifted from plan")
    _validate_batch_sizes(
        int(plan.get("relationship_batch_events") or 0),
        int(plan.get("entity_batch_applications") or 0),
    )
    return epoch


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def load_progress(path: Path, plan_sha256: str) -> ActivationProgress:
    if not path.exists():
        return ActivationProgress(plan_sha256=plan_sha256)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != PROGRESS_VERSION:
        raise RuntimeError("activation progress version is unsupported")
    progress = ActivationProgress(**dict(payload.get("progress") or {}))
    if progress.plan_sha256 != plan_sha256:
        raise RuntimeError("activation progress belongs to a different plan")
    return progress


def save_progress(path: Path, progress: ActivationProgress) -> None:
    _write_json_atomic(path, {"version": PROGRESS_VERSION, "progress": asdict(progress)})


def _sql_text(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _relationship_range_clause(after_hash: str, boundary_hash: str, max_source_rank: int) -> str:
    clauses = [
        f"event_type IN ({_event_type_sql()})",
        f"source_rank <= {int(max_source_rank)}",
        f"event_hash <= {_sql_text(boundary_hash)}",
    ]
    if after_hash:
        clauses.append(f"event_hash > {_sql_text(after_hash)}")
    return " AND ".join(clauses)


def relationship_boundary(
    client: Any, *, after_hash: str, max_source_rank: int, batch_size: int
) -> str | None:
    after = f"AND event_hash > {_sql_text(after_hash)}" if after_hash else ""
    rows = _query_rows(
        client,
        f"""
        SELECT max(toString(event_hash))
        FROM
        (
            SELECT event_hash
            FROM {SOURCE_TABLE} FINAL
            WHERE event_type IN ({_event_type_sql()})
              AND source_rank <= {int(max_source_rank)}
              {after}
            ORDER BY event_hash
            LIMIT {int(batch_size)}
        )
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError("CN relationship backfill boundary query failed")
    boundary = _text(rows[0][0]).strip()
    return boundary or None


def relationship_batch_stats(
    client: Any,
    *,
    table: str,
    after_hash: str,
    boundary_hash: str,
    max_source_rank: int,
    final: bool,
) -> dict[str, int]:
    if table not in {SOURCE_TABLE, RELATIONSHIP_TABLE}:
        raise ValueError("unsupported relationship statistics table")
    if table == SOURCE_TABLE:
        where = _relationship_range_clause(after_hash, boundary_hash, max_source_rank)
    else:
        clauses = [
            f"source_rank <= {int(max_source_rank)}",
            f"event_hash <= {_sql_text(boundary_hash)}",
        ]
        if after_hash:
            clauses.append(f"event_hash > {_sql_text(after_hash)}")
        where = " AND ".join(clauses)
    rows = _query_rows(
        client,
        f"""
        SELECT count(), groupBitXor(cityHash64(event_hash)), max(source_rank)
        FROM {table}{' FINAL' if final else ''}
        WHERE {where}
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError("CN relationship batch statistics query failed")
    return {
        "row_count": int(rows[0][0] or 0),
        "event_hash_xor": int(rows[0][1] or 0),
        "max_source_rank": int(rows[0][2] or 0),
    }


def insert_relationship_batch(
    client: Any, *, after_hash: str, boundary_hash: str, max_source_rank: int
) -> None:
    where = _relationship_range_clause(after_hash, boundary_hash, max_source_rank)
    client.command(
        f"""
        INSERT INTO {RELATIONSHIP_TABLE}
        SELECT event_id, application_number, event_type, event_date, observed_at,
               field_name, old_value_compact, new_value_compact, evidence_level,
               source_package_id, source_package_kind, source_file,
               source_first_line, source_last_line, source_row_hash, source_rank, event_hash
        FROM {SOURCE_TABLE} FINAL
        WHERE {where}
        """
    )


def entity_application_boundary(
    client: Any, *, after_application: str, max_source_rank: int, batch_size: int
) -> str | None:
    after = (
        f"AND application_number > {_sql_text(after_application)}" if after_application else ""
    )
    rows = _query_rows(
        client,
        f"""
        SELECT application_number
        FROM {RELATIONSHIP_TABLE} FINAL
        WHERE source_rank <= {int(max_source_rank)}
          {after}
        GROUP BY application_number
        ORDER BY application_number
        LIMIT {int(batch_size)}
        """,
        settings={"max_threads": 1, "optimize_aggregation_in_order": 1},
    )
    if not rows:
        return None
    return _text(rows[-1][0]).strip() or None


def _application_range_clause(after_application: str, boundary_application: str) -> str:
    clauses = [f"application_number <= {_sql_text(boundary_application)}"]
    if after_application:
        clauses.append(f"application_number > {_sql_text(after_application)}")
    return " AND ".join(clauses)


def entity_projection_select(
    *, after_application: str, boundary_application: str, max_source_rank: int
) -> str:
    application_range = _application_range_clause(after_application, boundary_application)
    return f"""
        WITH raw AS
        (
            SELECT
                application_number,
                multiIf(
                    startsWith(event_type, 'CO_OWNER_'), 'CO_OWNER',
                    startsWith(event_type, 'OWNER_'), 'OWNER',
                    'AGENT'
                ) AS role,
                toUInt8(position(event_type, 'SUPERSEDED') > 0) AS is_superseded,
                if(position(event_type, 'SUPERSEDED') > 0, old_value_compact, new_value_compact) AS fact_json,
                event_date,
                observed_at,
                source_package_id,
                source_package_kind,
                source_file,
                source_first_line,
                source_last_line,
                source_row_hash,
                source_rank,
                event_hash
            FROM {RELATIONSHIP_TABLE} FINAL
            WHERE source_rank <= {int(max_source_rank)}
              AND {application_range}
        ), normalized AS
        (
            SELECT *,
                   JSONExtractString(fact_json, 'relation_key') AS relation_key,
                   JSONExtractString(fact_json, 'entity_id') AS entity_id_candidate
            FROM raw
            WHERE JSONExtractString(fact_json, 'relation_key') != ''
        ), resolved AS
        (
            SELECT *,
                   argMaxIf(
                       entity_id_candidate,
                       tuple(source_rank, event_hash),
                       entity_id_candidate != ''
                   ) OVER (
                       PARTITION BY application_number, role, relation_key
                       ORDER BY source_rank, event_hash
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS entity_id_text
            FROM normalized
        )
        SELECT
            toUUID(entity_id_text) AS entity_id,
            role,
            application_number,
            relation_key,
            if(is_superseded = 1, 'SUPERSEDED', 'OBSERVED_CURRENT') AS action,
            event_date,
            observed_at,
            source_package_id,
            source_package_kind,
            source_file,
            source_first_line,
            source_last_line,
            source_row_hash,
            source_rank,
            event_hash
        FROM resolved
        WHERE entity_id_text != ''
    """


def _entity_fingerprint_expr() -> str:
    return """
        cityHash64(concat(
            toString(entity_id), '|', role, '|', application_number, '|',
            relation_key, '|', action, '|', ifNull(toString(event_date), ''), '|',
            toString(observed_at), '|', toString(source_package_id), '|',
            source_row_hash, '|', toString(source_rank), '|', event_hash
        ))
    """.strip()


def expected_entity_batch_stats(
    client: Any,
    *,
    after_application: str,
    boundary_application: str,
    max_source_rank: int,
) -> dict[str, int]:
    projection = entity_projection_select(
        after_application=after_application,
        boundary_application=boundary_application,
        max_source_rank=max_source_rank,
    )
    rows = _query_rows(
        client,
        f"SELECT count(), groupBitXor({_entity_fingerprint_expr()}) FROM ({projection})",
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError("CN entity expected batch statistics query failed")
    return {"row_count": int(rows[0][0] or 0), "row_hash_xor": int(rows[0][1] or 0)}


def actual_entity_batch_stats(
    client: Any,
    *,
    after_application: str,
    boundary_application: str,
    max_source_rank: int,
) -> dict[str, int]:
    application_range = _application_range_clause(after_application, boundary_application)
    rows = _query_rows(
        client,
        f"""
        SELECT count(), groupBitXor({_entity_fingerprint_expr()})
        FROM {ENTITY_TABLE} FINAL
        WHERE source_rank <= {int(max_source_rank)}
          AND {application_range}
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError("CN entity actual batch statistics query failed")
    return {"row_count": int(rows[0][0] or 0), "row_hash_xor": int(rows[0][1] or 0)}


def insert_entity_batch(
    client: Any,
    *,
    after_application: str,
    boundary_application: str,
    max_source_rank: int,
) -> None:
    projection = entity_projection_select(
        after_application=after_application,
        boundary_application=boundary_application,
        max_source_rank=max_source_rank,
    )
    client.command(f"INSERT INTO {ENTITY_TABLE} {projection}")


def _verified_equal(label: str, expected: Mapping[str, int], actual: Mapping[str, int]) -> None:
    if dict(expected) != dict(actual):
        raise RuntimeError(
            f"{label} completeness mismatch: expected={dict(expected)!r} actual={dict(actual)!r}"
        )


def benchmark_entity_lookup(client: Any, *, max_source_rank: int) -> dict[str, Any]:
    rows = _query_rows(
        client,
        f"""
        SELECT toString(entity_id)
        FROM {ENTITY_TABLE} FINAL
        WHERE source_rank <= {int(max_source_rank)}
        ORDER BY entity_id
        LIMIT 1
        """,
        settings={"max_threads": 1},
    )
    if not rows:
        return {"status": "EMPTY", "elapsed_ms": 0.0, "rows": 0}
    entity_id = _text(rows[0][0])
    started = time.perf_counter()
    probe = _query_rows(
        client,
        f"""
        SELECT role, application_number, relation_key, action, source_rank, event_hash
        FROM {ENTITY_TABLE} FINAL
        WHERE entity_id = toUUID({_sql_text(entity_id)})
          AND source_rank <= {int(max_source_rank)}
        ORDER BY entity_id, role, application_number, relation_key, event_hash
        LIMIT 101
        """,
        settings={"max_threads": 1, "max_result_rows": 101, "result_overflow_mode": "throw"},
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    if elapsed_ms > 5_000:
        raise RuntimeError(f"CN entity lookup benchmark exceeded 5000 ms: {elapsed_ms}")
    return {"status": "PASS", "entity_id": entity_id, "elapsed_ms": elapsed_ms, "rows": len(probe)}


def _write_readiness(
    client: Any,
    *,
    plan_sha256: str,
    implementation_sha: str,
    max_source_rank: int,
    max_event_hash: str,
) -> None:
    accepted = datetime.now(timezone.utc).isoformat()
    watermark = f"source_rank:{max_source_rank}:event_hash:{max_event_hash}"
    acceptance_hash = _sha256(
        {
            "plan_sha256": plan_sha256,
            "implementation_sha": implementation_sha,
            "source_watermark": watermark,
        }
    )
    client.command(
        f"""
        INSERT INTO {READINESS_TABLE}
        (ready_version, source_watermark, source_max_rank, implementation_sha, accepted_at, acceptance_hash)
        VALUES (
            {_sql_text(READY_VERSION)}, {_sql_text(watermark)}, {int(max_source_rank)},
            {_sql_text(implementation_sha)}, parseDateTime64BestEffort({_sql_text(accepted)}, 3),
            {_sql_text(acceptance_hash)}
        )
        """
    )
    client.command(
        f"""
        INSERT INTO markorbit_facts.schema_version (component, version)
        VALUES ('CN_RELATIONSHIP_TIMELINE', {_sql_text(RELATIONSHIP_TIMELINE_READY_VERSION)})
        """
    )


def execute_activation_plan(
    plan_path: Path,
    *,
    plan_sha: str,
    authority_token: str,
    progress_path: Path,
    receipt_path: Path,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, Any]:
    plan = load_activation_plan(plan_path, plan_sha)
    expected_token = f"GO #741 CN entity portfolio activation {plan_sha.lower()}"
    if authority_token.strip() != expected_token:
        raise PermissionError("fresh exact production authority token is required")
    target = client or clickhouse_client()
    validate_live_plan(
        plan,
        client=target,
        epoch_getter=current_cn_applicant_serving_epoch,
        main_sha_getter=main_sha_getter,
    )
    source_stats = dict(plan["source_stats"])
    max_source_rank = int(source_stats["max_source_rank"])
    progress = load_progress(progress_path, plan_sha.lower())
    relationship_batch_events = int(plan["relationship_batch_events"])
    entity_batch_applications = int(plan["entity_batch_applications"])
    try:
        while progress.stage == "RELATIONSHIP":
            boundary = relationship_boundary(
                target,
                after_hash=progress.after_event_hash,
                max_source_rank=max_source_rank,
                batch_size=relationship_batch_events,
            )
            if boundary is None:
                progress = ActivationProgress(
                    plan_sha256=progress.plan_sha256,
                    stage="ENTITY",
                    after_event_hash=progress.after_event_hash,
                    relationship_rows_verified=progress.relationship_rows_verified,
                )
                save_progress(progress_path, progress)
                break
            expected = relationship_batch_stats(
                target,
                table=SOURCE_TABLE,
                after_hash=progress.after_event_hash,
                boundary_hash=boundary,
                max_source_rank=max_source_rank,
                final=True,
            )
            insert_relationship_batch(
                target,
                after_hash=progress.after_event_hash,
                boundary_hash=boundary,
                max_source_rank=max_source_rank,
            )
            actual = relationship_batch_stats(
                target,
                table=RELATIONSHIP_TABLE,
                after_hash=progress.after_event_hash,
                boundary_hash=boundary,
                max_source_rank=max_source_rank,
                final=True,
            )
            _verified_equal("relationship batch", expected, actual)
            progress = ActivationProgress(
                plan_sha256=progress.plan_sha256,
                stage="RELATIONSHIP",
                after_event_hash=boundary,
                relationship_rows_verified=progress.relationship_rows_verified + expected["row_count"],
            )
            save_progress(progress_path, progress)

        while progress.stage == "ENTITY":
            boundary = entity_application_boundary(
                target,
                after_application=progress.after_application_number,
                max_source_rank=max_source_rank,
                batch_size=entity_batch_applications,
            )
            if boundary is None:
                progress = ActivationProgress(
                    plan_sha256=progress.plan_sha256,
                    stage="COMPLETE",
                    after_event_hash=progress.after_event_hash,
                    after_application_number=progress.after_application_number,
                    relationship_rows_verified=progress.relationship_rows_verified,
                    entity_rows_verified=progress.entity_rows_verified,
                    entity_batches_verified=progress.entity_batches_verified,
                )
                save_progress(progress_path, progress)
                break
            expected = expected_entity_batch_stats(
                target,
                after_application=progress.after_application_number,
                boundary_application=boundary,
                max_source_rank=max_source_rank,
            )
            insert_entity_batch(
                target,
                after_application=progress.after_application_number,
                boundary_application=boundary,
                max_source_rank=max_source_rank,
            )
            actual = actual_entity_batch_stats(
                target,
                after_application=progress.after_application_number,
                boundary_application=boundary,
                max_source_rank=max_source_rank,
            )
            _verified_equal("entity batch", expected, actual)
            progress = ActivationProgress(
                plan_sha256=progress.plan_sha256,
                stage="ENTITY",
                after_event_hash=progress.after_event_hash,
                after_application_number=boundary,
                relationship_rows_verified=progress.relationship_rows_verified,
                entity_rows_verified=progress.entity_rows_verified + expected["row_count"],
                entity_batches_verified=progress.entity_batches_verified + 1,
            )
            save_progress(progress_path, progress)

        if progress.stage != "COMPLETE":
            raise RuntimeError("CN entity activation did not reach COMPLETE")
        if progress.relationship_rows_verified != int(source_stats["row_count"]):
            raise RuntimeError("verified relationship row count does not match frozen source")
        benchmark = benchmark_entity_lookup(target, max_source_rank=max_source_rank)
        implementation_sha = str(plan["implementation_sha"])
        _write_readiness(
            target,
            plan_sha256=plan_sha.lower(),
            implementation_sha=implementation_sha,
            max_source_rank=max_source_rank,
            max_event_hash=str(source_stats["max_event_hash"]),
        )
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "plan_sha256": plan_sha.lower(),
            "implementation_sha": implementation_sha,
            "source_stats": source_stats,
            "progress": asdict(progress),
            "benchmark": benchmark,
            "readiness_version": READY_VERSION,
        }
    except Exception as exc:
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "FAILED",
            "plan_sha256": plan_sha.lower(),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "progress": asdict(progress),
        }
        _write_json_atomic(receipt_path, receipt)
        raise
    _write_json_atomic(receipt_path, receipt)
    return receipt


def _default_output(prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).resolve().parents[2] / "reports" / f"{prefix}_{stamp}.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exact-plan gated CN entity portfolio activation")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path)
    prepare.add_argument("--relationship-batch-events", type=int, default=DEFAULT_RELATIONSHIP_BATCH_EVENTS)
    prepare.add_argument("--entity-batch-applications", type=int, default=DEFAULT_ENTITY_BATCH_APPLICATIONS)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--plan-sha", required=True)
    apply.add_argument("--authority-token", required=True)
    apply.add_argument("--progress", type=Path)
    apply.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        path = args.output or _default_output("production_cn_entity_portfolio_activation_plan")
        result = prepare_activation_plan(
            path,
            relationship_batch_events=args.relationship_batch_events,
            entity_batch_applications=args.entity_batch_applications,
        )
        print(json.dumps({"plan_path": str(path), **result}, indent=2, sort_keys=True))
        return 0
    progress = args.progress or _default_output("production_cn_entity_portfolio_activation_progress")
    receipt = args.receipt or _default_output("production_cn_entity_portfolio_activation_receipt")
    result = execute_activation_plan(
        args.plan,
        plan_sha=args.plan_sha,
        authority_token=args.authority_token,
        progress_path=progress,
        receipt_path=receipt,
    )
    print(json.dumps({"receipt_path": str(receipt), **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
