from __future__ import annotations

from typing import Any

from app.cn.text import normalized_match_text
from app.read_performance_baseline import DEFAULT_QUERY_BUDGET


AGENT_NAME_LOOKUP_SCHEMA_VERSION = "CN_AGENT_NAME_LOOKUP_SCHEMA_V1"
AGENT_NAME_LOOKUP_READY_VERSION = "CN_AGENT_NAME_LOOKUP_READY_V1"
CN_AGENT_NAME_LOOKUP_TABLE = "markorbit_facts.cn_agent_name_candidate_lookup"
MAX_AGENT_MATCHES = 500


class AgentNameLookupInvalid(ValueError):
    pass


class AgentNameLookupScopeExceeded(RuntimeError):
    pass


class AgentNameLookupUnavailable(RuntimeError):
    pass


def normalize_agent_name(value: str) -> str:
    raw_name = str(value or "").strip()
    if not raw_name or len(raw_name) > 512:
        raise AgentNameLookupInvalid("agent name must contain 1 to 512 characters")
    normalized_name = normalized_match_text(raw_name)
    if not normalized_name:
        raise AgentNameLookupInvalid("agent name has no searchable characters")
    return normalized_name


def _sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _rows(client: Any, sql: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        result = client.query(sql, settings=settings)
    except Exception as exc:
        raise AgentNameLookupUnavailable(str(exc)) from exc
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def agents_by_name(client: Any, name: str) -> dict[str, Any]:
    normalized_name = normalize_agent_name(name)
    budget = {**DEFAULT_QUERY_BUDGET, "max_result_rows": MAX_AGENT_MATCHES + 1}
    readiness = _rows(
        client,
        """
        SELECT version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'CN_AGENT_NAME_LOOKUP'
        LIMIT 1
        """,
        budget,
    )
    if not readiness or str(readiness[0]["version"]) != AGENT_NAME_LOOKUP_READY_VERSION:
        raise AgentNameLookupUnavailable("CN agent name lookup is not backfilled and accepted")

    candidates = _rows(
        client,
        f"""
        SELECT agent_code
        FROM {CN_AGENT_NAME_LOOKUP_TABLE} FINAL
        WHERE normalized_name = {_sql_literal(normalized_name)}
          AND is_deleted = 0
        ORDER BY normalized_name, agent_code
        LIMIT {MAX_AGENT_MATCHES + 1}
        """,
        budget,
    )
    if len(candidates) > MAX_AGENT_MATCHES:
        raise AgentNameLookupScopeExceeded(
            f"agent name resolves to more than {MAX_AGENT_MATCHES} candidate records"
        )
    candidate_codes = list(dict.fromkeys(str(row["agent_code"]) for row in candidates))
    matches: list[dict[str, Any]] = []
    if candidate_codes:
        code_sql = ", ".join(_sql_literal(value) for value in candidate_codes)
        matches = _rows(
            client,
            f"""
            SELECT agent_code, toString(mention_id) AS mention_id,
                   toString(entity_id) AS entity_id, agent_name, source_file,
                   source_first_line, source_last_line, source_row_hash,
                   toString(last_source_package_id) AS source_package_id,
                   source_rank, ingested_at
            FROM markorbit_facts.cn_agent_current FINAL
            WHERE agent_code IN ({code_sql})
              AND agent_name_norm = {_sql_literal(normalized_name)}
              AND is_deleted = 0
            ORDER BY agent_code
            LIMIT {MAX_AGENT_MATCHES}
            """,
            budget,
        )
    return {
        "input_name": str(name).strip(),
        "normalized_name": normalized_name,
        "candidate_count": len(candidate_codes),
        "match_count": len(matches),
        "matches": matches,
        "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_NAME_FACTS_NO_IDENTITY_RESOLUTION",
    }
