from __future__ import annotations

from typing import Any

from app.cn import audit_data, audit_followup
from app.cn.audit_acceptance import build_acceptance_audit as acceptance_main
from app.cn.resource_client import CNResourceClient, cn_resource_client
from app.db import clickhouse_execution_settings


CN_ACCEPTANCE_JOIN_ALGORITHM = "grace_hash"
CN_ACCEPTANCE_GRACE_HASH_JOIN_INITIAL_BUCKETS = 32
CN_ACCEPTANCE_SEND_RECEIVE_TIMEOUT = 3600
CN_ACCEPTANCE_PARTY_UNIQUENESS_BUCKETS = 64


class CNAcceptanceResourceClient(CNResourceClient):
    """CN acceptance client with spill-safe exact uniqueness checks.

    ``audit_data._clickhouse_integrity`` historically used ``uniqExact`` across the
    entire retained corpus. ``uniqExact`` keeps its hash state in memory and does
    not benefit from ClickHouse's external GROUP BY spill controls, so the real
    target host can hit the 8 GiB per-query envelope even after JOINs are moved to
    ``grace_hash``. The acceptance path preserves the exact duplicate-excess
    semantics while expressing the check as GROUP BY + ``sum(count() - 1)`` so
    ClickHouse can spill aggregation state to disk.

    The party-current key space is substantially larger than case/scope. Its exact
    GROUP BY is therefore partitioned deterministically by a hash of the complete
    uniqueness key and summed across buckets. Every equal key hashes to the same
    bucket, so duplicate-excess semantics remain exact while peak aggregation state
    is bounded to one bucket at a time.
    """

    _UNIQUENESS_MARKER = "uniqExact(application_number)"

    @staticmethod
    def _duplicate_excess_query(
        table: str,
        keys: str,
        where: str,
        bucket_predicate: str | None = None,
    ) -> str:
        predicates = [f"({where})"]
        if bucket_predicate:
            predicates.append(f"({bucket_predicate})")
        return f"""
        SELECT coalesce(sum(group_count - 1), 0)
        FROM
        (
            SELECT count() AS group_count
            FROM {table} FINAL
            WHERE {' AND '.join(predicates)}
            GROUP BY {keys}
            HAVING group_count > 1
        )
        """

    @staticmethod
    def _party_bucket_predicate(bucket: int) -> str:
        return (
            "cityHash64(application_number, role, relation_key) % "
            f"{CN_ACCEPTANCE_PARTY_UNIQUENESS_BUCKETS} = {bucket}"
        )

    def _party_duplicate_excess(self) -> int:
        duplicate_excess = 0
        for bucket in range(CN_ACCEPTANCE_PARTY_UNIQUENESS_BUCKETS):
            value = super().query(
                self._duplicate_excess_query(
                    "markorbit_facts.cn_case_party_current",
                    "application_number, role, relation_key",
                    "is_deleted = 0 AND is_current = 1",
                    self._party_bucket_predicate(bucket),
                )
            ).result_rows[0][0]
            duplicate_excess += int(value or 0)
        return duplicate_excess

    def query(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if self._UNIQUENESS_MARKER not in sql:
            return super().query(sql, *args, **kwargs)

        case_duplicates = super().query(
            self._duplicate_excess_query(
                "markorbit_facts.cn_case_current",
                "application_number",
                "is_deleted = 0",
            )
        ).result_rows[0][0]
        scope_duplicates = super().query(
            self._duplicate_excess_query(
                "markorbit_facts.cn_case_scope_current",
                "application_number, class_no",
                "is_deleted = 0",
            )
        ).result_rows[0][0]
        party_duplicates = self._party_duplicate_excess()

        class _Result:
            result_rows = [
                (
                    int(case_duplicates or 0),
                    int(scope_duplicates or 0),
                    int(party_duplicates or 0),
                )
            ]

        return _Result()


def build_acceptance_audit_m16() -> dict[str, Any]:
    """Run the real CN acceptance audit under the proven M1.6 resource profile.

    The final acceptance scans the accumulated current corpus and includes anti-join
    integrity checks. On a production-sized corpus those joins can exceed the server
    memory ceiling when ClickHouse chooses an in-memory hash join. Reuse the same
    disk-spilling JOIN profile and per-query CN envelope already proven by ingestion,
    contract preflight, and the non-empty runtime fixture.

    ``audit_data`` and ``audit_followup`` import ``clickhouse_client`` directly, so
    both module-local factories are wrapped for the duration of the acceptance run.
    They are always restored, including when the audit fails.
    """
    original_audit_client = audit_data.clickhouse_client
    original_followup_client = audit_followup.clickhouse_client
    audit_data.clickhouse_client = lambda: CNAcceptanceResourceClient(
        original_audit_client()
    )
    audit_followup.clickhouse_client = lambda: cn_resource_client(original_followup_client)
    try:
        with clickhouse_execution_settings(
            join_algorithm=CN_ACCEPTANCE_JOIN_ALGORITHM,
            grace_hash_join_initial_buckets=(
                CN_ACCEPTANCE_GRACE_HASH_JOIN_INITIAL_BUCKETS
            ),
            send_receive_timeout=CN_ACCEPTANCE_SEND_RECEIVE_TIMEOUT,
        ):
            return acceptance_main()
    finally:
        audit_data.clickhouse_client = original_audit_client
        audit_followup.clickhouse_client = original_followup_client


def main() -> int:
    import json

    result = build_acceptance_audit_m16()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in {"PASS", "PASS_WITH_WARNINGS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
