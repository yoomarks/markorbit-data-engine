from __future__ import annotations

import re
from typing import Any


_EVENT_TARGET = "INSERT INTO markorbit_facts.cn_observed_event"
_GOODS_BASELINE_MARKER = "'GOODS_SCOPE_OBSERVED'"
_GOODS_DELTA_MARKER = "'GOODS_SCOPE_CHANGED_OBSERVED'"
_DERIVED_BASELINE_MARKER = "'DERIVED_CASE_OBSERVED'"
_GOODS_WHERE = re.compile(
    r"WHERE \(cur\.application_number = '' OR cur\.source_rank < (?P<rank>\d+)\)\s+"
    r"AND \(cur\.application_number = '' OR cur\.scope_hash != incoming\.scope_hash\)"
)


class EventBaselineDeltaClient:
    """Narrow M1.6 adapter that prevents baseline-only event duplication.

    Case-level baseline suppression is handled by ``insert_case_delta_events``.
    This adapter handles the two baseline event INSERTs embedded directly in the
    legacy publisher: goods-scope first observations and derived-case structural
    observations. Party relation events are deliberately untouched.
    """

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._goods_rewrite_count = 0
        self._derived_skip_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def goods_rewrite_count(self) -> int:
        return self._goods_rewrite_count

    @property
    def derived_skip_count(self) -> int:
        return self._derived_skip_count

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if _EVENT_TARGET not in sql:
            return self._delegate.command(sql, *args, **kwargs)

        if _DERIVED_BASELINE_MARKER in sql:
            if self._derived_skip_count != 0:
                raise RuntimeError(
                    "Storage V2 saw multiple DERIVED_CASE_OBSERVED INSERTs in one CN publish."
                )
            self._derived_skip_count += 1
            return None

        if _GOODS_BASELINE_MARKER in sql and _GOODS_DELTA_MARKER in sql:
            if self._goods_rewrite_count != 0:
                raise RuntimeError(
                    "Storage V2 saw multiple CN goods-scope event INSERTs in one publish."
                )
            rewritten, count = _GOODS_WHERE.subn(
                "WHERE cur.application_number != ''\n"
                "          AND cur.source_rank < \\g<rank>\n"
                "          AND cur.scope_hash != incoming.scope_hash",
                sql,
            )
            if count != 1:
                raise RuntimeError(
                    "Legacy CN goods-scope event SQL shape changed; refusing baseline "
                    f"suppression because expected one predicate, found {count}."
                )
            self._goods_rewrite_count += 1
            return self._delegate.command(rewritten, *args, **kwargs)

        return self._delegate.command(sql, *args, **kwargs)

    def assert_rewrite_counts(self) -> None:
        if self._goods_rewrite_count != 1 or self._derived_skip_count != 1:
            raise RuntimeError(
                "Storage V2 expected one goods-scope baseline rewrite and one derived-case "
                "baseline skip per legacy CN publish; got "
                f"goods={self._goods_rewrite_count}, derived={self._derived_skip_count}."
            )


def insert_case_delta_events(
    client: Any,
    package: str,
    package_kind: str,
    source_rank: int,
    case_agg: str,
) -> None:
    """Publish only case-level events that compare against prior current state."""
    common_join = f"""
        FROM ({case_agg}) AS incoming
        LEFT JOIN markorbit_facts.cn_case_current AS cur FINAL
          ON cur.application_number = incoming.application_number
        WHERE cur.application_number != ''
          AND cur.source_rank < {source_rank}
    """

    event_specs = [
        (
            "'CASE_FACTS_CHANGED_OBSERVED'",
            "incoming.filing_date",
            "CASE",
            "record_hash",
            "cur.record_hash != incoming.record_hash",
            "cur.record_hash",
            "incoming.record_hash",
        ),
        (
            "'PRELIMINARY_PUBLICATION_OBSERVED'",
            "incoming.prelim_pub_date",
            "CASE",
            "preliminary_publication",
            "incoming.prelim_pub_date IS NOT NULL AND concat(ifNull(toString(cur.prelim_pub_date), ''), '|', cur.prelim_pub_issue) != concat(ifNull(toString(incoming.prelim_pub_date), ''), '|', incoming.prelim_pub_issue)",
            "toJSONString(map('date', ifNull(toString(cur.prelim_pub_date), ''), 'issue', cur.prelim_pub_issue))",
            "toJSONString(map('date', ifNull(toString(incoming.prelim_pub_date), ''), 'issue', incoming.prelim_pub_issue))",
        ),
        (
            "'REGISTRATION_PUBLICATION_OBSERVED'",
            "incoming.registration_pub_date",
            "CASE",
            "registration_publication",
            "incoming.registration_pub_date IS NOT NULL AND concat(ifNull(toString(cur.registration_pub_date), ''), '|', cur.registration_pub_issue) != concat(ifNull(toString(incoming.registration_pub_date), ''), '|', incoming.registration_pub_issue)",
            "toJSONString(map('date', ifNull(toString(cur.registration_pub_date), ''), 'issue', cur.registration_pub_issue))",
            "toJSONString(map('date', ifNull(toString(incoming.registration_pub_date), ''), 'issue', incoming.registration_pub_issue))",
        ),
        (
            "if(cur.valid_until IS NOT NULL AND incoming.exclusive_end_date > cur.valid_until, 'TERM_EXTENDED_OBSERVED', 'EXCLUSIVE_TERM_OBSERVED')",
            "incoming.exclusive_end_date",
            "CASE",
            "exclusive_term",
            "(incoming.exclusive_start_date IS NOT NULL OR incoming.exclusive_end_date IS NOT NULL) AND concat(ifNull(toString(cur.valid_from), ''), '|', ifNull(toString(cur.valid_until), '')) != concat(ifNull(toString(incoming.exclusive_start_date), ''), '|', ifNull(toString(incoming.exclusive_end_date), ''))",
            "toJSONString(map('from', ifNull(toString(cur.valid_from), ''), 'until', ifNull(toString(cur.valid_until), '')))",
            "toJSONString(map('from', ifNull(toString(incoming.exclusive_start_date), ''), 'until', ifNull(toString(incoming.exclusive_end_date), ''), 'raw', incoming.exclusive_period))",
        ),
        (
            "'MARK_NAME_CHANGED_OBSERVED'",
            "CAST(NULL, 'Nullable(Date32)')",
            "CASE",
            "mark_name",
            "cur.mark_name_raw != incoming.mark_name_raw",
            "cur.mark_name_raw",
            "incoming.mark_name_raw",
        ),
        (
            "'AGENT_CODE_CHANGED_OBSERVED'",
            "CAST(NULL, 'Nullable(Date32)')",
            "PARTY",
            "agent_code",
            "cur.agent_code != incoming.agent_code",
            "cur.agent_code",
            "incoming.agent_code",
        ),
    ]

    for event_type, event_date, scope, field_name, condition, old_value, new_value in event_specs:
        client.command(
            f"""
            INSERT INTO markorbit_facts.cn_observed_event
            SELECT
                generateUUIDv4(), incoming.case_id, incoming.application_number,
                {event_type}, {event_date}, now64(3), '{scope}',
                CAST(NULL, 'Nullable(UInt8)'), '{field_name}',
                {old_value}, {new_value}, 'OFFICIAL_FACT_OBSERVATION',
                'NOT_DETERMINED', 1.0, toUUID('{package}'), '{package_kind}',
                incoming.source_file, incoming.source_first_line, incoming.source_last_line,
                incoming.source_row_hash, {source_rank},
                hex(SHA256(concat(
                    incoming.application_number, '|', {event_type}, '|', '{field_name}', '|',
                    {old_value}, '|', {new_value}, '|', toString({source_rank})
                )))
            {common_join}
              AND ({condition})
            """
        )
