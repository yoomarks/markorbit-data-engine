from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.read_performance_baseline import DEFAULT_QUERY_BUDGET


MAX_AGENT_CODE_LENGTH = 128
READ_SETTINGS = {**DEFAULT_QUERY_BUDGET, "max_result_rows": 2}


class AgentExactReadInvalid(ValueError):
    pass


class AgentExactReadUnavailable(RuntimeError):
    pass


def _agent_code(value: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > MAX_AGENT_CODE_LENGTH:
        raise AgentExactReadInvalid(
            f"agent_code must contain 1 to {MAX_AGENT_CODE_LENGTH} characters"
        )
    return result


def _sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    return str(value or "")


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        observed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        try:
            observed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AgentExactReadUnavailable("agent ingested_at is not an ISO timestamp") from exc
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _row(client: Any, code: str) -> Mapping[str, Any] | None:
    try:
        result = client.query(
            f"""
            SELECT agent_code, toString(mention_id) AS mention_id,
                   toString(entity_id) AS entity_id, agent_name, agent_name_norm,
                   source_file, source_first_line, source_last_line, source_row_hash,
                   toString(last_source_package_id) AS source_package_id,
                   source_rank, ingested_at
            FROM markorbit_facts.cn_agent_current FINAL
            WHERE agent_code = {_sql_literal(code)}
              AND is_deleted = 0
            ORDER BY source_rank DESC
            LIMIT 2
            """,
            settings=READ_SETTINGS,
        )
    except Exception as exc:
        raise AgentExactReadUnavailable(str(exc)) from exc
    rows = [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
    if len(rows) > 1:
        raise AgentExactReadUnavailable(
            "CN agent exact read returned multiple current rows for one agent_code"
        )
    return rows[0] if rows else None


def read_agent_exact(client: Any, agent_code: str) -> dict[str, Any]:
    code = _agent_code(agent_code)
    row = _row(client, code)
    if row is None:
        return {
            "agent_code": code,
            "record": None,
            "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_CODE_FACT_NO_IDENTITY_RESOLUTION",
        }

    entity_id = _text(row.get("entity_id")).strip()
    source_rank = int(row.get("source_rank") or 0)
    source_hash = _text(row.get("source_row_hash")).strip().lower()
    observed_at = _iso(row.get("ingested_at"))
    if source_rank <= 0:
        raise AgentExactReadUnavailable("CN agent exact read has no positive source_rank")
    if len(source_hash) != 64 or any(ch not in "0123456789abcdef" for ch in source_hash):
        raise AgentExactReadUnavailable("CN agent exact read has invalid source_row_hash")

    record = {
        "agent_code": code,
        "mention_id": _text(row.get("mention_id")).strip(),
        "entity_id": entity_id or None,
        "agent_name": _text(row.get("agent_name")).strip(),
        "agent_name_norm": _text(row.get("agent_name_norm")).strip(),
        "source_file": _text(row.get("source_file")).strip(),
        "source_first_line": int(row.get("source_first_line") or 0),
        "source_last_line": int(row.get("source_last_line") or 0),
        "source_package_id": _text(row.get("source_package_id")).strip(),
        "source_rank": source_rank,
        "ingested_at": observed_at,
        "source_reference": {
            "owner": "DATA_ENGINE",
            "kind": "CN_AGENT",
            "id": code,
            "version": str(source_rank),
            "fingerprintSha256": source_hash,
            "observedAt": observed_at,
        },
        "currentness": {
            "state": "CURRENT_SOURCE_FACT",
            "source_rank": source_rank,
            "observed_at": observed_at,
            "is_deleted": False,
        },
        "legal_identity_verified": False,
        "customer_relationship_established": False,
        "professional_appointment_established": False,
    }
    return {
        "agent_code": code,
        "record": record,
        "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_CODE_FACT_NO_IDENTITY_RESOLUTION",
    }
