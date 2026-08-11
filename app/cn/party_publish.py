from __future__ import annotations

from collections.abc import Callable
from typing import Any
import uuid

from app.cn.goods_lifecycle import ApplicationRange
from app.db import clickhouse_client


PARTY_PUBLISH_TARGET_BASIC_ROWS = 250_000

_PARTY_PUBLISH_STAGE_DDL = """
CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_party_publish
(
    package_id UUID,
    case_id UUID,
    application_number String,
    role LowCardinality(String),
    relation_id UUID,
    relation_key FixedString(64),
    mention_id UUID,
    entity_id Nullable(UUID),
    agent_code String,
    raw_name String,
    normalized_name String,
    raw_address String,
    normalized_address String,
    country_code String,
    region_code String,
    city String,
    class_nos Array(UInt8),
    confidence_score Float32,
    source_file String,
    source_first_line UInt64,
    source_last_line UInt64,
    source_row_hash FixedString(64),
    record_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, application_number, role, relation_key)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE
"""


class PartyHistoryDeltaClient:
    """Narrow adapter that makes legacy OBSERVED_CURRENT history delta-only.

    M1.6 deliberately reuses the proven legacy publisher after materializing a
    bounded party snapshot. The legacy publisher historically appended one
    ``OBSERVED_CURRENT`` history row for every relation in every package, even
    when the relation was unchanged. That creates permanent no-op history at
    corpus scale.

    This adapter rewrites exactly that one INSERT so its predicate matches the
    already-delta-aware party observed-event predicate: persist a relation when
    it is first seen, reactivated, or materially changed. All other commands and
    queries pass through untouched. The adapter fails closed if the expected
    legacy SQL shape changes or if the target INSERT is seen more than once.
    """

    _TARGET_TABLE = "INSERT INTO markorbit_facts.cn_case_party_relation_history"
    _TARGET_ACTION = "'OBSERVED_CURRENT'"

    def __init__(self, delegate: Any, *, source_rank: int) -> None:
        self._delegate = delegate
        self._source_rank = int(source_rank)
        self._rewrite_count = 0

    @property
    def rewrite_count(self) -> int:
        return self._rewrite_count

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if self._TARGET_TABLE in sql and self._TARGET_ACTION in sql:
            sql = self._rewrite_observed_current(sql)
        return self._delegate.command(sql, *args, **kwargs)

    def assert_observed_current_rewritten(self) -> None:
        if self._rewrite_count != 1:
            raise RuntimeError(
                "Storage V2 expected exactly one CN party OBSERVED_CURRENT history "
                f"INSERT, rewrote {self._rewrite_count}. Legacy publisher shape changed."
            )

    def _rewrite_observed_current(self, sql: str) -> str:
        if self._rewrite_count != 0:
            raise RuntimeError(
                "Storage V2 encountered multiple CN party OBSERVED_CURRENT history "
                "INSERTs; refusing ambiguous publisher behavior."
            )

        stripped = sql.rstrip()
        if not stripped.endswith("AS incoming"):
            raise RuntimeError(
                "Legacy CN party OBSERVED_CURRENT SQL shape changed; expected the "
                "history INSERT to end with 'AS incoming'."
            )
        if "cn_case_party_current AS cur FINAL" in stripped:
            raise RuntimeError(
                "Legacy CN party OBSERVED_CURRENT history already contains a current "
                "relation join; refusing to apply Storage V2 twice."
            )

        self._rewrite_count += 1
        return stripped + f"""
        LEFT JOIN markorbit_facts.cn_case_party_current AS cur FINAL
          ON cur.application_number = incoming.application_number
         AND cur.role = incoming.role
         AND cur.relation_key = incoming.relation_key
        WHERE (cur.application_number = '' OR cur.source_rank < {self._source_rank})
          AND (
              cur.application_number = ''
              OR cur.is_current = 0
              OR cur.record_hash != incoming.record_hash
          )
        """


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def ensure_party_publish_schema(*, client: Any | None = None) -> None:
    (client or clickhouse_client()).command(_PARTY_PUBLISH_STAGE_DDL)


def _plan_party_application_ranges(
    package_uuid: uuid.UUID | str,
    *,
    client: Any | None = None,
    target_rows: int = PARTY_PUBLISH_TARGET_BASIC_ROWS,
) -> list[ApplicationRange]:
    """Plan whole-application ranges from physically ordered basic-stage rows."""
    if target_rows < 1:
        raise ValueError("target_rows must be positive")

    client = client or clickhouse_client()
    package = str(package_uuid)
    ranges: list[ApplicationRange] = []
    lower: str | None = None

    while True:
        lower_sql = ""
        if lower is not None:
            lower_sql = f" AND application_number >= {_sql_string(lower)}"

        rows = client.query(
            f"""
            SELECT application_number
            FROM markorbit_facts.cn_stage_basic
            WHERE package_id = toUUID('{package}'){lower_sql}
            ORDER BY application_number
            LIMIT 1 OFFSET {int(target_rows)}
            """
        ).result_rows

        if not rows:
            ranges.append(ApplicationRange(lower=lower, upper=None))
            break

        boundary = str(rows[0][0])
        if lower is not None and boundary <= lower:
            next_rows = client.query(
                f"""
                SELECT application_number
                FROM markorbit_facts.cn_stage_basic
                WHERE package_id = toUUID('{package}')
                  AND application_number > {_sql_string(lower)}
                ORDER BY application_number
                LIMIT 1
                """
            ).result_rows
            if not next_rows:
                ranges.append(ApplicationRange(lower=lower, upper=None))
                break
            boundary = str(next_rows[0][0])

        ranges.append(ApplicationRange(lower=lower, upper=boundary))
        lower = boundary

    return ranges


def _source_filter(package: str, application_range: ApplicationRange) -> str:
    return (
        f"WHERE package_id = toUUID('{package}')"
        f"{application_range.and_predicate('application_number')}"
    )


def bounded_party_aggregate_sql(
    package_uuid: uuid.UUID | str,
    application_range: ApplicationRange,
    aggregate_builder: Callable[[str], str],
) -> str:
    """Reuse the legacy party aggregate while pushing range filters to sources.

    The exact legacy aggregate remains the semantic authority for role mapping,
    class aggregation, lineage, and record hashes. This function only injects
    package/application predicates into its four physical stage sources. It
    fails closed if the expected SQL shape changes so a future legacy refactor
    cannot silently bypass bounded execution.
    """
    package = str(package_uuid)
    sql = aggregate_builder(package)
    source_filter = _source_filter(package, application_range)

    replacements = [
        (
            "FROM markorbit_facts.cn_stage_applicant\n            ) AS applicant_source",
            "FROM markorbit_facts.cn_stage_applicant\n"
            f"                {source_filter}\n"
            "            ) AS applicant_source",
            1,
        ),
        (
            "FROM markorbit_facts.cn_stage_coowner\n            ) AS co",
            "FROM markorbit_facts.cn_stage_coowner\n"
            f"                {source_filter}\n"
            "            ) AS co",
            1,
        ),
        (
            "FROM markorbit_facts.cn_stage_basic\n            ) AS b",
            "FROM markorbit_facts.cn_stage_basic\n"
            f"                {source_filter}\n"
            "            ) AS b",
            2,
        ),
        (
            "FROM markorbit_facts.cn_stage_agent\n            ) AS a",
            "FROM markorbit_facts.cn_stage_agent\n"
            f"                WHERE package_id = toUUID('{package}')\n"
            "            ) AS a",
            1,
        ),
    ]

    for old, new, expected_count in replacements:
        actual_count = sql.count(old)
        if actual_count != expected_count:
            raise RuntimeError(
                "Legacy party aggregate SQL shape changed; bounded source filter "
                f"expected {expected_count} occurrence(s) of {old!r}, found {actual_count}."
            )
        sql = sql.replace(old, new)

    # Keep the original package guards and add explicit range guards at each
    # grouping branch. This is redundant with the source predicates by design:
    # it makes the bounded invariant visible to the optimizer and to tests.
    range_predicate = application_range.predicate("application_number")
    if range_predicate:
        owner_guard = f"WHERE package_id = toUUID('{package}')"
        sql = sql.replace(
            owner_guard,
            f"{owner_guard} AND {range_predicate}",
            1,
        )

        co_guard = f"WHERE co.package_id = toUUID('{package}')"
        sql = sql.replace(
            co_guard,
            f"{co_guard}{application_range.and_predicate('co.application_number')}",
            1,
        )

        agent_guard = f"WHERE b.package_id = toUUID('{package}')"
        sql = sql.replace(
            agent_guard,
            f"{agent_guard}{application_range.and_predicate('b.application_number')}",
            1,
        )

    return sql


def party_publish_stage_sql(package_uuid: uuid.UUID | str) -> str:
    package = str(package_uuid)
    return f"""
        SELECT
            case_id, application_number, role, relation_id, relation_key,
            mention_id, entity_id, agent_code, raw_name, normalized_name,
            raw_address, normalized_address, country_code, region_code, city,
            class_nos, confidence_score, source_file, source_first_line,
            source_last_line, source_row_hash, record_hash
        FROM markorbit_facts.cn_stage_party_publish
        WHERE package_id = toUUID('{package}')
    """


def cleanup_party_publish_stage(
    package_uuid: uuid.UUID | str,
    *,
    client: Any | None = None,
) -> None:
    client = client or clickhouse_client()
    package = str(package_uuid)
    client.command(
        "ALTER TABLE markorbit_facts.cn_stage_party_publish "
        f"DELETE WHERE package_id = toUUID('{package}') SETTINGS mutations_sync = 1"
    )


def materialize_party_publish_stage(
    package_uuid: uuid.UUID | str,
    aggregate_builder: Callable[[str], str],
    *,
    client: Any | None = None,
    target_rows: int = PARTY_PUBLISH_TARGET_BASIC_ROWS,
) -> dict[str, int]:
    """Aggregate PARTY facts once, in bounded whole-application chunks."""
    client = client or clickhouse_client()
    package = str(package_uuid)
    ensure_party_publish_schema(client=client)
    cleanup_party_publish_stage(package_uuid, client=client)

    application_ranges = _plan_party_application_ranges(
        package_uuid,
        client=client,
        target_rows=target_rows,
    )

    for application_range in application_ranges:
        party_sql = bounded_party_aggregate_sql(
            package_uuid,
            application_range,
            aggregate_builder,
        )
        client.command(f"""
            INSERT INTO markorbit_facts.cn_stage_party_publish
            (
                package_id, case_id, application_number, role, relation_id,
                relation_key, mention_id, entity_id, agent_code, raw_name,
                normalized_name, raw_address, normalized_address, country_code,
                region_code, city, class_nos, confidence_score, source_file,
                source_first_line, source_last_line, source_row_hash, record_hash
            )
            SELECT
                toUUID('{package}'), incoming.case_id, incoming.application_number,
                incoming.role, incoming.relation_id, incoming.relation_key,
                incoming.mention_id, incoming.entity_id, incoming.agent_code,
                incoming.raw_name, incoming.normalized_name, incoming.raw_address,
                incoming.normalized_address, incoming.country_code,
                incoming.region_code, incoming.city, incoming.class_nos,
                incoming.confidence_score, incoming.source_file,
                incoming.source_first_line, incoming.source_last_line,
                incoming.source_row_hash, incoming.record_hash
            FROM ({party_sql}) AS incoming
        """)

    row_count = int(
        client.query(
            "SELECT count() FROM markorbit_facts.cn_stage_party_publish "
            f"WHERE package_id = toUUID('{package}')"
        ).result_rows[0][0]
        or 0
    )
    return {
        "party_publish_rows": row_count,
        "party_publish_chunk_count": len(application_ranges),
    }
