import argparse
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from app.db import postgres_conn
from app.us.applicant_candidate_backfill_control import (
    USApplicantServingEpoch,
    current_us_applicant_serving_epoch,
)
from app.us.natural_lapse_discovery import (
    ADMITTED_DESCRIPTION,
    ADMITTED_EVENT_CODE,
    ADMITTED_EVENT_TYPE_CODE,
    ADMITTED_REASON,
    PROJECTION_JOB_TYPE,
    PROJECTION_TABLE,
    accepted_projection_state,
)
from app.us.target_bulk_tasks import latest_complete_target_bulk_epoch
from app.us.target_canary import (
    TARGET_DATABASE,
    TARGET_HTTP_PORT,
    TARGET_NATIVE_HOST,
    WslNativeClickHouseClient,
)

PLAN_VERSION = "US_NATURAL_LAPSE_PROJECTION_PLAN_V2"
RECEIPT_VERSION = "US_NATURAL_LAPSE_PROJECTION_RECEIPT_V2"
SCHEMA_VERSION = "US_NATURAL_LAPSE_DISCOVERY_V1"
HTTP_TIMEOUT_SECONDS = 1_800
CHECKSUM_DEFINITION = "EVENT_KEY_CLASS_SERIAL_CITYHASH64_V2"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BACKFILL_LOCK = "markorbit:us:natural-lapse-projection"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ddl_path() -> Path:
    return _repo_root() / "database" / "clickhouse" / "init" / "014_us_natural_lapse_discovery.sql"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_main_sha(repo_root: Path | None = None) -> str:
    root = repo_root or _repo_root()
    sha = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8")
        .strip()
        .lower()
    )
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=root, text=True, encoding="utf-8"
    ).strip()
    if branch != "main" or not _HEX40.fullmatch(sha):
        raise RuntimeError("production natural-lapse operator requires exact local main")
    return sha


class TargetNaturalLapseClient:
    def __init__(self, base: WslNativeClickHouseClient | None = None) -> None:
        self._base = base or WslNativeClickHouseClient(
            connection=http.client.HTTPConnection(
                TARGET_NATIVE_HOST, TARGET_HTTP_PORT, timeout=HTTP_TIMEOUT_SECONDS
            )
        )

    def query(self, sql: str):
        return self._base.query(sql)

    def command(self, sql: str) -> str:
        return self._base.command(sql)


def _row(result: Any) -> list[Any]:
    rows = list(result.result_rows)
    if len(rows) != 1:
        raise RuntimeError("expected exactly one ClickHouse result row")
    return list(rows[0])


SOURCE_MANIFEST_VERSION = "US_NATURAL_LAPSE_SOURCE_MANIFEST_V2"


def source_manifest_entries(client: Any) -> list[dict[str, Any]]:
    rows = client.query(
        """
        SELECT lineage_kind, toString(package_id), package_kind,
               ifNull(toString(effective_date), ''), source_file,
               min(source_rank), max(source_rank), count()
        FROM
        (
            SELECT 'CASE' AS lineage_kind, last_source_package_id AS package_id,
                   source_package_kind AS package_kind, source_effective_date AS effective_date,
                   source_file, source_rank
            FROM markorbit_facts.us_case_current FINAL WHERE is_deleted = 0
            UNION ALL
            SELECT 'EVENT', source_package_id, source_package_kind,
                   source_effective_date, source_file, source_rank
            FROM markorbit_facts.us_event_history FINAL
            UNION ALL
            SELECT 'CLASS', last_source_package_id, source_package_kind,
                   source_effective_date, source_file, source_rank
            FROM markorbit_facts.us_classification_current FINAL WHERE is_deleted = 0
            UNION ALL
            SELECT 'OWNER', last_source_package_id, source_package_kind,
                   source_effective_date, source_file, source_rank
            FROM markorbit_facts.us_owner_current FINAL WHERE is_deleted = 0
        )
        GROUP BY lineage_kind, package_id, package_kind, effective_date, source_file
        ORDER BY lineage_kind, effective_date, source_file, package_id
        SETTINGS max_threads = 4, max_execution_time = 300,
                 max_memory_usage = 2147483648, use_uncompressed_cache = 0
        """
    ).result_rows
    entries = [
        {
            "lineage_kind": str(row[0]),
            "source_package_id": str(row[1]),
            "source_package_kind": str(row[2]),
            "source_effective_date": str(row[3] or ""),
            "source_file": str(row[4]),
            "min_source_rank": int(row[5]),
            "max_source_rank": int(row[6]),
            "fact_row_count": int(row[7]),
        }
        for row in rows
    ]
    if not entries:
        raise RuntimeError("accepted US target source-lineage manifest is empty")
    return entries


def current_source_manifest(
    epoch: USApplicantServingEpoch, *, client: Any
) -> dict[str, Any]:
    accepted = latest_complete_target_bulk_epoch()
    if accepted is None:
        raise RuntimeError("no accepted US target bulk epoch is available")
    if (
        str(accepted.get("run_id") or "") != epoch.bulk_run_id
        or str(accepted.get("plan_sha256") or "") != epoch.plan_sha256
        or int(accepted.get("checkpoint_sequence") or 0) != epoch.checkpoint_sequence
    ):
        raise RuntimeError("accepted US target bulk epoch drifted while resolving source manifest")
    entries = source_manifest_entries(client)
    package_ids = sorted({str(item["source_package_id"]) for item in entries})
    payload = {
        "version": SOURCE_MANIFEST_VERSION,
        "accepted_bulk_run_id": epoch.bulk_run_id,
        "accepted_plan_sha256": epoch.plan_sha256,
        "accepted_checkpoint_sequence": epoch.checkpoint_sequence,
        "relevant_package_count": len(package_ids),
        "relevant_package_ids_sha256": _sha256(package_ids),
        "entry_count": len(entries),
        "entries": entries,
    }
    return {**payload, "fingerprint": _sha256(payload)}


def current_source_manifest_fingerprint(
    epoch: USApplicantServingEpoch, *, client: Any
) -> str:
    return str(current_source_manifest(epoch, client=client)["fingerprint"])


def _class_projection_sql() -> str:
    return """
        SELECT serial_number,
               arraySort(arrayDistinct(arrayFilter(
                   x -> x >= 1 AND x <= 45,
                   arrayMap(x -> toUInt8OrZero(x), arrayFlatten(groupArray(international_codes)))
               ))) AS nice_classes,
               toJSONString(arraySort(groupArray(tuple(
                   toString(classification_key),
                   source_package_kind,
                   ifNull(toString(source_effective_date), ''),
                   source_file,
                   source_row_hash,
                   toString(last_source_package_id),
                   record_hash,
                   source_rank
               )))) AS class_lineage_json,
               max(source_rank) AS class_source_rank
        FROM markorbit_facts.us_classification_current FINAL
        WHERE is_deleted = 0
        GROUP BY serial_number
    """


def _owner_projection_sql() -> str:
    return """
        SELECT serial_number,
               arraySort(groupUniqArrayIf(party_name, party_name != '')) AS owner_names,
               toJSONString(arraySort(groupArray(tuple(
                   toString(owner_key),
                   source_package_kind,
                   ifNull(toString(source_effective_date), ''),
                   source_file,
                   source_row_hash,
                   toString(last_source_package_id),
                   record_hash,
                   source_rank
               )))) AS owner_lineage_json,
               max(source_rank) AS owner_source_rank
        FROM markorbit_facts.us_owner_current FINAL
        WHERE is_deleted = 0
        GROUP BY serial_number
    """


def _source_key_select() -> str:
    return f"""
        SELECT e.event_key AS event_key,
               e.serial_number AS serial_number,
               e.event_date AS event_date,
               nice_class
        FROM markorbit_facts.us_event_history FINAL AS e
        INNER JOIN markorbit_facts.us_case_current FINAL AS c
            ON c.serial_number = e.serial_number AND c.is_deleted = 0
        INNER JOIN ({_class_projection_sql()}) AS cls
            ON cls.serial_number = e.serial_number
        ARRAY JOIN cls.nice_classes AS nice_class
        WHERE e.event_code = '{ADMITTED_EVENT_CODE}'
          AND e.event_type_code = '{ADMITTED_EVENT_TYPE_CODE}'
          AND e.description_text = '{ADMITTED_DESCRIPTION}'
          AND e.event_date IS NOT NULL
    """


def source_identity_sql() -> str:
    return f"""
        SELECT count() AS rows,
               uniqExact(event_key) AS event_count,
               uniqExact(serial_number) AS serial_count,
               sum(cityHash64(toJSONString(tuple(event_key, nice_class, serial_number)))) AS checksum_sum,
               groupBitXor(cityHash64(toJSONString(tuple(event_key, nice_class, serial_number)))) AS checksum_xor,
               min(event_date) AS min_event_date,
               max(event_date) AS max_event_date
        FROM ({_source_key_select()})
        SETTINGS max_threads = 4, max_execution_time = 1800,
                 max_memory_usage = 4294967296, use_uncompressed_cache = 0
    """


def projection_identity_sql(snapshot_id: str) -> str:
    return f"""
        SELECT count() AS rows,
               uniqExact(lapse_event_key) AS event_count,
               uniqExact(serial_number) AS serial_count,
               sum(cityHash64(toJSONString(tuple(lapse_event_key, nice_class, serial_number)))) AS checksum_sum,
               groupBitXor(cityHash64(toJSONString(tuple(lapse_event_key, nice_class, serial_number)))) AS checksum_xor,
               min(lapse_event_date) AS min_event_date,
               max(lapse_event_date) AS max_event_date
        FROM {PROJECTION_TABLE} FINAL
        WHERE projection_snapshot_id = '{snapshot_id}'
          AND lapse_reason = '{ADMITTED_REASON}'
          AND jurisdiction = 'US'
          AND legal_conclusion = 0
        SETTINGS max_threads = 2, max_execution_time = 900,
                 max_memory_usage = 2147483648, use_uncompressed_cache = 0
    """


def _identity(client: Any, sql: str) -> dict[str, Any]:
    row = _row(client.query(sql))
    return {
        "rows": int(row[0]),
        "event_count": int(row[1]),
        "serial_count": int(row[2]),
        "checksum_sum": int(row[3]),
        "checksum_xor": int(row[4]),
        "min_event_date": str(row[5] or ""),
        "max_event_date": str(row[6] or ""),
        "checksum_definition": CHECKSUM_DEFINITION,
    }


def source_identity(client: Any) -> dict[str, Any]:
    identity = _identity(client, source_identity_sql())
    if identity["rows"] <= 0 or identity["event_count"] <= 0:
        raise RuntimeError("accepted US target contains no source-proven natural-lapse cohort rows")
    return identity


def projection_identity(client: Any, snapshot_id: str) -> dict[str, Any]:
    return _identity(client, projection_identity_sql(snapshot_id))


def projection_snapshot_id(
    *,
    epoch: USApplicantServingEpoch,
    source_manifest_fingerprint: str,
    source: Mapping[str, Any],
) -> str:
    return _sha256(
        {
            "schema_version": SCHEMA_VERSION,
            "source_epoch": epoch.to_dict(),
            "source_manifest_fingerprint": source_manifest_fingerprint,
            "source_identity": dict(source),
        }
    )


def projection_insert_sql(*, snapshot_id: str, source_manifest_fingerprint: str) -> str:
    if not _HEX64.fullmatch(snapshot_id) or not _HEX64.fullmatch(source_manifest_fingerprint):
        raise ValueError("projection snapshot and source manifest must be exact SHA-256 values")
    return f"""
        INSERT INTO {PROJECTION_TABLE}
        (
            jurisdiction, lapse_reason, lapse_event_date, nice_class,
            lapse_event_key, serial_number, registration_number,
            mark_identification, mark_drawing_code, owner_names,
            observed_status_code, observed_status_date, cancellation_date,
            renewal_date, event_code, event_type_code, description_text,
            event_source_package_kind, event_source_effective_date,
            event_source_file, event_source_row_hash, event_source_package_id,
            event_source_rank, event_observed_at, case_source_package_kind,
            case_source_effective_date, case_source_file, case_source_row_hash,
            case_source_package_id, case_record_hash, case_source_rank,
            class_lineage_json, owner_lineage_json,
            source_manifest_fingerprint, projection_snapshot_id,
            legal_conclusion, projection_rank
        )
        SELECT 'US' AS jurisdiction,
               '{ADMITTED_REASON}' AS lapse_reason,
               e.event_date AS lapse_event_date,
               nice_class,
               e.event_key AS lapse_event_key,
               e.serial_number,
               c.registration_number,
               c.mark_identification,
               c.mark_drawing_code,
               ifNull(owners.owner_names, CAST([], 'Array(String)')),
               c.status_code,
               c.status_date,
               c.cancellation_date,
               c.renewal_date,
               e.event_code,
               e.event_type_code,
               e.description_text,
               e.source_package_kind,
               e.source_effective_date,
               e.source_file,
               e.source_row_hash,
               e.source_package_id,
               e.source_rank,
               e.observed_at,
               c.source_package_kind,
               c.source_effective_date,
               c.source_file,
               c.source_row_hash,
               c.last_source_package_id,
               c.record_hash,
               c.source_rank,
               cls.class_lineage_json,
               ifNull(owners.owner_lineage_json, '[]'),
               '{source_manifest_fingerprint}',
               '{snapshot_id}',
               toUInt8(0),
               greatest(
                   e.source_rank,
                   c.source_rank,
                   cls.class_source_rank,
                   ifNull(owners.owner_source_rank, toUInt64(0))
               ) AS projection_rank
        FROM markorbit_facts.us_event_history FINAL AS e
        INNER JOIN markorbit_facts.us_case_current FINAL AS c
            ON c.serial_number = e.serial_number AND c.is_deleted = 0
        INNER JOIN ({_class_projection_sql()}) AS cls
            ON cls.serial_number = e.serial_number
        LEFT JOIN ({_owner_projection_sql()}) AS owners
            ON owners.serial_number = e.serial_number
        ARRAY JOIN cls.nice_classes AS nice_class
        WHERE e.event_code = '{ADMITTED_EVENT_CODE}'
          AND e.event_type_code = '{ADMITTED_EVENT_TYPE_CODE}'
          AND e.description_text = '{ADMITTED_DESCRIPTION}'
          AND e.event_date IS NOT NULL
        SETTINGS max_threads = 4, max_execution_time = 1800,
                 max_memory_usage = 4294967296, use_uncompressed_cache = 0
    """


def _table_inventory(client: Any) -> dict[str, Any] | None:
    rows = client.query(
        f"""
        SELECT name, engine, sorting_key, total_rows
        FROM system.tables
        WHERE database = '{TARGET_DATABASE}'
          AND name = 'us_natural_lapse_discovery_current'
        """
    ).result_rows
    if not rows:
        return None
    row = rows[0]
    return {
        "name": str(row[0]),
        "engine": str(row[1]),
        "sorting_key": str(row[2]),
        "total_rows": int(row[3] or 0),
    }


def _snapshot_row_count(client: Any, snapshot_id: str) -> int:
    if _table_inventory(client) is None:
        return 0
    return int(
        _row(
            client.query(
                f"SELECT count() FROM {PROJECTION_TABLE} FINAL "
                f"WHERE projection_snapshot_id = '{snapshot_id}'"
            )
        )[0]
    )


def _ddl_statements() -> list[str]:
    statements = [
        item.strip() for item in _ddl_path().read_text(encoding="utf-8").split(";") if item.strip()
    ]
    if len(statements) != 2:
        raise RuntimeError("natural-lapse DDL must contain exactly two statements")
    if not statements[0].startswith("CREATE TABLE IF NOT EXISTS"):
        raise RuntimeError("natural-lapse projection DDL is malformed")
    if not statements[1].startswith("INSERT INTO markorbit_facts.schema_version"):
        raise RuntimeError("natural-lapse schema marker DDL is malformed")
    return statements


def _target_schema_state(client: Any) -> dict[str, Any]:
    row = _table_inventory(client)
    if row is None:
        raise RuntimeError("natural-lapse target table is missing")
    normalized = str(row["sorting_key"]).replace("`", "").replace("(", "").replace(")", "").strip()
    expected = (
        "lapse_reason, lapse_event_date, nice_class, lapse_event_key, serial_number, "
        "projection_snapshot_id"
    )
    if row["engine"] != "ReplacingMergeTree" or normalized != expected:
        raise RuntimeError("natural-lapse target schema drifted")
    marker = _row(
        client.query(
            """
            SELECT count()
            FROM markorbit_facts.schema_version FINAL
            WHERE component = 'US_NATURAL_LAPSE_DISCOVERY'
              AND version = 'US_NATURAL_LAPSE_DISCOVERY_V1'
            """
        )
    )
    if int(marker[0]) != 1:
        raise RuntimeError("natural-lapse target schema marker is missing")
    return row


def _identity_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    keys = {
        "rows",
        "event_count",
        "serial_count",
        "checksum_sum",
        "checksum_xor",
        "min_event_date",
        "max_event_date",
        "checksum_definition",
    }
    return all(left.get(key) == right.get(key) for key in keys)


def prepare_plan(
    output_path: Path,
    *,
    client: Any | None = None,
    epoch_getter: Callable[[], USApplicantServingEpoch] = current_us_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, Any]:
    target = client or TargetNaturalLapseClient()
    main_sha = main_sha_getter().lower()
    if not _HEX40.fullmatch(main_sha):
        raise RuntimeError("natural-lapse plan requires an exact implementation SHA")
    epoch = epoch_getter()
    manifest = current_source_manifest(epoch, client=target)
    manifest_fingerprint = str(manifest["fingerprint"])
    source = source_identity(target)
    snapshot_id = projection_snapshot_id(
        epoch=epoch,
        source_manifest_fingerprint=manifest_fingerprint,
        source=source,
    )
    plan = {
        "version": PLAN_VERSION,
        "expected_main": main_sha,
        "implementation_sha": main_sha,
        "source_epoch": epoch.to_dict(),
        "source_manifest": manifest,
        "source_manifest_fingerprint": manifest_fingerprint,
        "source_identity": source,
        "projection_snapshot_id": snapshot_id,
        "ddl_sha256": _file_sha256(_ddl_path()),
        "target_prestate": {
            "table": _table_inventory(target),
            "snapshot_rows": _snapshot_row_count(target, snapshot_id),
            "accepted_state": accepted_projection_state(),
        },
        "mutation_scope": {
            "clickhouse_tables": [PROJECTION_TABLE, "markorbit_facts.schema_version"],
            "postgres_job_type": PROJECTION_JOB_TYPE,
            "operations": [
                "CREATE_IF_NOT_EXISTS",
                "INSERT_IMMUTABLE_PROJECTION_SNAPSHOT",
                "PUBLISH_ACCEPTANCE_STATE",
            ],
            "forbidden": ["ALTER", "DELETE", "DROP", "TRUNCATE", "OPTIMIZE", "MOVE"],
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return envelope


def load_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    expected_sha = str(expected_sha or "").lower()
    if not _HEX64.fullmatch(expected_sha):
        raise ValueError("plan SHA must be 64 lowercase hexadecimal characters")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"plan", "plan_sha256"} or not isinstance(envelope["plan"], dict):
        raise RuntimeError("malformed natural-lapse plan envelope")
    computed = _sha256(envelope["plan"])
    if computed != expected_sha or envelope["plan_sha256"] != computed:
        raise RuntimeError("natural-lapse plan SHA mismatch")
    if envelope["plan"].get("version") != PLAN_VERSION:
        raise RuntimeError("unsupported natural-lapse plan version")
    return dict(envelope["plan"])


def _start_run(plan: Mapping[str, Any], plan_sha: str) -> str:
    payload = {
        "plan_sha256": plan_sha,
        "implementation_sha": plan["implementation_sha"],
        "source_epoch": plan["source_epoch"],
        "source_manifest_fingerprint": plan["source_manifest_fingerprint"],
        "projection_snapshot_id": plan["projection_snapshot_id"],
        "production_mutation_authorized": True,
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_BACKFILL_LOCK,))
            cur.execute(
                "SELECT count(*) AS n FROM control.job_run WHERE job_type = %s AND status = 'RUNNING'",
                (PROJECTION_JOB_TYPE,),
            )
            if int((cur.fetchone() or {}).get("n", 0) or 0):
                raise RuntimeError("natural-lapse projection backfill is already RUNNING")
            cur.execute(
                """
                INSERT INTO control.job_run (job_type, trigger_type, status, payload, metrics)
                VALUES (%s, 'OPERATOR', 'RUNNING', %s::jsonb, '{}'::jsonb)
                RETURNING run_id
                """,
                (PROJECTION_JOB_TYPE, json.dumps(payload, sort_keys=True)),
            )
            run_id = str(cur.fetchone()["run_id"])
        conn.commit()
    return run_id


def _finish_run(
    run_id: str,
    *,
    status: str,
    metrics: Mapping[str, Any],
    error_message: str | None = None,
) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.job_run
                SET status = %s,
                    finished_at = now(),
                    metrics = %s::jsonb,
                    error_message = %s
                WHERE run_id = %s AND job_type = %s AND status = 'RUNNING'
                RETURNING run_id
                """,
                (
                    status,
                    json.dumps(dict(metrics), sort_keys=True, default=str),
                    error_message,
                    run_id,
                    PROJECTION_JOB_TYPE,
                ),
            )
            if cur.fetchone() is None:
                raise RuntimeError("natural-lapse projection run finalization was rejected")
        conn.commit()


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def execute_plan(
    plan_path: Path,
    *,
    plan_sha: str,
    authority_token: str,
    receipt_path: Path,
    client: Any | None = None,
    epoch_getter: Callable[[], USApplicantServingEpoch] = current_us_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, Any]:
    plan = load_plan(plan_path, plan_sha)
    expected_authority = f"GO #700 US natural lapse projection backfill {plan_sha}"
    if authority_token != expected_authority:
        raise PermissionError("exact #700 production authority token is required")
    main_sha = main_sha_getter().lower()
    if main_sha != plan.get("expected_main") or main_sha != plan.get("implementation_sha"):
        raise RuntimeError("natural-lapse execution main drifted from frozen plan")
    if _file_sha256(_ddl_path()) != plan.get("ddl_sha256"):
        raise RuntimeError("natural-lapse DDL drifted from frozen plan")

    target = client or TargetNaturalLapseClient()
    epoch = epoch_getter()
    if epoch.to_dict() != dict(plan.get("source_epoch") or {}):
        raise RuntimeError("accepted US serving epoch drifted from frozen plan")
    manifest = current_source_manifest(epoch, client=target)
    manifest_fingerprint = str(manifest["fingerprint"])
    if manifest != dict(plan.get("source_manifest") or {}):
        raise RuntimeError("accepted US source manifest drifted from frozen plan")
    if manifest_fingerprint != plan.get("source_manifest_fingerprint"):
        raise RuntimeError("accepted US source manifest fingerprint drifted from frozen plan")
    source_before = source_identity(target)
    if not _identity_equal(source_before, dict(plan.get("source_identity") or {})):
        raise RuntimeError("natural-lapse source identity drifted from frozen plan")
    snapshot_id = projection_snapshot_id(
        epoch=epoch,
        source_manifest_fingerprint=manifest_fingerprint,
        source=source_before,
    )
    if snapshot_id != plan.get("projection_snapshot_id"):
        raise RuntimeError("natural-lapse projection snapshot identity drifted from frozen plan")

    accepted_state = accepted_projection_state()
    if accepted_state is not None and accepted_state.get("projection_snapshot_id") == snapshot_id:
        projection = projection_identity(target, snapshot_id)
        if not _identity_equal(source_before, projection):
            raise RuntimeError("accepted natural-lapse state exists but projection parity failed")
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "ALREADY_ACCEPTED",
            "implementation_sha": main_sha,
            "plan_sha256": plan_sha,
            "source_epoch": epoch.to_dict(),
            "source_manifest_fingerprint": manifest_fingerprint,
            "projection_snapshot_id": snapshot_id,
            "source_identity": source_before,
            "projection_identity": projection,
            "authority_consumed": False,
            "copy_executed": False,
        }
        _write_receipt(receipt_path, receipt)
        return receipt

    if _snapshot_row_count(target, snapshot_id) != 0:
        raise RuntimeError(
            "unaccepted rows already exist for this natural-lapse snapshot; remediation required"
        )

    run_id = _start_run(plan, plan_sha)
    try:
        for statement in _ddl_statements():
            target.command(statement)
        _target_schema_state(target)
        if _snapshot_row_count(target, snapshot_id) != 0:
            raise RuntimeError("natural-lapse snapshot was not empty immediately before backfill")

        target.command(
            projection_insert_sql(
                snapshot_id=snapshot_id,
                source_manifest_fingerprint=manifest_fingerprint,
            )
        )
        projection = projection_identity(target, snapshot_id)
        if not _identity_equal(source_before, projection):
            raise RuntimeError("natural-lapse projection failed exact source identity parity")
        source_after = source_identity(target)
        if not _identity_equal(source_before, source_after):
            raise RuntimeError("natural-lapse source identity changed during backfill")
        after_epoch = epoch_getter()
        if after_epoch != epoch:
            raise RuntimeError("accepted US serving epoch changed during natural-lapse backfill")
        if current_source_manifest(after_epoch, client=target) != manifest:
            raise RuntimeError("accepted US source manifest changed during natural-lapse backfill")

        disks = _row(
            target.query(
                """
                SELECT groupUniqArray(disk_name)
                FROM system.parts
                WHERE database = 'markorbit_facts'
                  AND table = 'us_natural_lapse_discovery_current'
                  AND active
                """
            )
        )[0]
        if sorted(str(item) for item in disks) != ["hot_us"]:
            raise RuntimeError(f"natural-lapse projection is not isolated to hot_us: {disks}")

        metrics = {
            "source_identity": source_before,
            "projection_identity": projection,
            "target_disks": ["hot_us"],
            "legal_conclusion": False,
        }
        _finish_run(run_id, status="SUCCESS", metrics=metrics)
        completed_at = datetime.now(timezone.utc).isoformat()
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "run_id": run_id,
            "implementation_sha": main_sha,
            "plan_sha256": plan_sha,
            "source_epoch": epoch.to_dict(),
            "source_manifest_fingerprint": manifest_fingerprint,
            "projection_snapshot_id": snapshot_id,
            "source_identity": source_before,
            "projection_identity": projection,
            "target_disks": ["hot_us"],
            "authority_consumed": True,
            "copy_executed": True,
            "completed_at": completed_at,
        }
        _write_receipt(receipt_path, receipt)
        return receipt
    except Exception as exc:
        try:
            _finish_run(
                run_id,
                status="FAILED",
                metrics={"projection_snapshot_id": snapshot_id},
                error_message=f"{type(exc).__name__}: {exc}",
            )
        except Exception as finish_exc:
            raise RuntimeError(
                f"natural-lapse backfill failed and control finalization also failed: {finish_exc}"
            ) from exc
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="US natural-lapse projection operator")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--plan-sha", required=True)
    apply.add_argument("--authority-token", required=True)
    apply.add_argument("--receipt", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "prepare":
        envelope = prepare_plan(args.output)
        print(json.dumps(envelope, sort_keys=True))
        print("US_NATURAL_LAPSE_PROJECTION_READY_FOR_EXACT_GO")
        return 0
    receipt = execute_plan(
        args.plan,
        plan_sha=args.plan_sha,
        authority_token=args.authority_token,
        receipt_path=args.receipt,
    )
    print(json.dumps(receipt, sort_keys=True, default=str))
    print("US_NATURAL_LAPSE_PROJECTION_ACCEPTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
