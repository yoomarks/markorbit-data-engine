from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import metadata
from typing import Any, Iterable, Iterator, Mapping

from app.research_dataset import ResearchDatasetRefV1, build_research_dataset_ref_v1


DATASET_NAME = "CN_FILING_TO_PRELIM_PUBLICATION_DURATION_V1"
FACT_SCHEMA_VERSION = "CN_CASE_CURRENT_FILING_TO_PRELIM_DURATION_V1"
SOURCE_TABLE = "markorbit_facts.cn_case_current"
DEFAULT_BATCH_SIZE = 5_000
DEFAULT_MAX_ROWS = 10_000
_MAX_BATCH_SIZE = 100_000


@dataclass(frozen=True)
class DurationObservation:
    application_number: str
    filing_date: date
    prelim_pub_date: date
    duration_days: int | None
    quality: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_number": self.application_number,
            "filing_date": self.filing_date.isoformat(),
            "prelim_pub_date": self.prelim_pub_date.isoformat(),
            "duration_days": self.duration_days,
            "quality": self.quality,
        }


@dataclass(frozen=True)
class DurationDatasetMaterialization:
    dataset_ref: ResearchDatasetRefV1
    valid_rows: int
    invalid_date_order_rows: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_ref": self.dataset_ref.to_dict(),
            "quality": {
                "valid_rows": self.valid_rows,
                "invalid_date_order_rows": self.invalid_date_order_rows,
                "missing_temporal_fields": "EXCLUDED_BY_DECLARED_QUERY_POLICY",
                "negative_duration_coercion": False,
            },
        }


def _as_required_date(value: Any, field: str) -> date:
    if value is None or value == "":
        raise ValueError(f"{field} is required by the research query")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def normalize_duration_observation(row: Mapping[str, Any]) -> DurationObservation:
    application_number = str(row.get("application_number") or "").strip()
    if not application_number:
        raise ValueError("application_number is required")

    filing_date = _as_required_date(row.get("filing_date"), "filing_date")
    prelim_pub_date = _as_required_date(row.get("prelim_pub_date"), "prelim_pub_date")
    if prelim_pub_date < filing_date:
        return DurationObservation(
            application_number=application_number,
            filing_date=filing_date,
            prelim_pub_date=prelim_pub_date,
            duration_days=None,
            quality="INVALID_DATE_ORDER",
        )
    return DurationObservation(
        application_number=application_number,
        filing_date=filing_date,
        prelim_pub_date=prelim_pub_date,
        duration_days=(prelim_pub_date - filing_date).days,
        quality="VALID",
    )


def _canonical_row_bytes(observation: DurationObservation) -> bytes:
    payload = json.dumps(
        observation.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{payload}\n".encode("utf-8")


class DurationIntegrityAccumulator:
    """Batch-boundary-independent digest over canonical application-number order."""

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._last_application_number: str | None = None
        self.row_count = 0
        self.valid_rows = 0
        self.invalid_date_order_rows = 0

    def add(self, observation: DurationObservation) -> None:
        if (
            self._last_application_number is not None
            and observation.application_number <= self._last_application_number
        ):
            raise ValueError(
                "duration research rows must be strictly ordered by application_number"
            )
        self._digest.update(_canonical_row_bytes(observation))
        self._last_application_number = observation.application_number
        self.row_count += 1
        if observation.quality == "VALID":
            self.valid_rows += 1
        elif observation.quality == "INVALID_DATE_ORDER":
            self.invalid_date_order_rows += 1
        else:
            raise ValueError(f"unsupported duration quality: {observation.quality}")

    @property
    def integrity_sha256(self) -> str:
        return self._digest.hexdigest()


def _query_identity(*, max_rows: int | None) -> dict[str, Any]:
    return {
        "dataset": DATASET_NAME,
        "source_table": SOURCE_TABLE,
        "selected_fields": ["application_number", "filing_date", "prelim_pub_date"],
        "source_predicate": {
            "is_deleted": 0,
            "filing_date": "NOT_NULL",
            "prelim_pub_date": "NOT_NULL",
        },
        "derived_fields": {
            "duration_days": "CALENDAR_DAYS(prelim_pub_date-filing_date)",
            "quality": ["VALID", "INVALID_DATE_ORDER"],
        },
        "missing_temporal_policy": "EXCLUDE_DECLARED",
        "invalid_date_order_policy": "RETAIN_WITH_NULL_DURATION_AND_QUALITY_FLAG",
        "ordering": ["application_number ASC"],
        "population_bound": (
            None
            if max_rows is None
            else {"strategy": "ORDERED_PREFIX", "max_rows": max_rows}
        ),
        "legal_conclusion": False,
        "actionability": "SOURCE_FACT_ONLY",
    }


def materialize_duration_dataset(
    rows: Iterable[Mapping[str, Any]],
    *,
    engine_version: str,
    watermark: str,
    generated_at: str,
    max_rows: int | None = None,
    fact_schema_version: str = FACT_SCHEMA_VERSION,
) -> DurationDatasetMaterialization:
    if max_rows is not None and max_rows <= 0:
        raise ValueError("max_rows must be positive when provided")

    accumulator = DurationIntegrityAccumulator()
    for raw_row in rows:
        if max_rows is not None and accumulator.row_count >= max_rows:
            break
        accumulator.add(normalize_duration_observation(raw_row))

    dataset_ref = build_research_dataset_ref_v1(
        engine_version=engine_version,
        fact_schema_version=fact_schema_version,
        jurisdictions=["CN"],
        resource_kinds=["FACT"],
        query=_query_identity(max_rows=max_rows),
        watermark=watermark,
        completeness="COMPLETE_BOUNDED" if max_rows is not None else "COMPLETE_TO_WATERMARK",
        pagination={
            "strategy": "KEYSET",
            "order_by": ["application_number ASC"],
            "cursor_field": "application_number",
            "execution_batch_size_in_replay_identity": False,
        },
        aggregation=None,
        sampling=None,
        partition=None,
        row_count=accumulator.row_count,
        generated_at=generated_at,
        integrity_sha256=accumulator.integrity_sha256,
    )
    return DurationDatasetMaterialization(
        dataset_ref=dataset_ref,
        valid_rows=accumulator.valid_rows,
        invalid_date_order_rows=accumulator.invalid_date_order_rows,
    )


def _coverage_date() -> date:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT max(COALESCE(dataset_release_date, source_period_end)) AS coverage_date
                FROM control.source_package
                WHERE jurisdiction = 'CN'
                  AND status = 'SUCCESS'
                  AND package_kind = 'MONTHLY_PATCH'
                """
            )
            row = cur.fetchone()
    value = None if row is None else row.get("coverage_date")
    if value is None:
        raise RuntimeError("CN research has no accepted MONTHLY_PATCH data clock")
    return _as_required_date(value, "coverage_date")


def _duration_batch_sql(*, after_application_number: str, batch_size: int) -> str:
    if batch_size <= 0 or batch_size > _MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {_MAX_BATCH_SIZE}")
    cursor = after_application_number.replace("'", "''")
    return f"""
        SELECT
            application_number,
            filing_date,
            prelim_pub_date
        FROM {SOURCE_TABLE} FINAL
        WHERE is_deleted = 0
          AND filing_date IS NOT NULL
          AND prelim_pub_date IS NOT NULL
          AND application_number > '{cursor}'
        ORDER BY application_number ASC
        LIMIT {int(batch_size)}
    """


def _dict_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def iter_live_duration_rows(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rows: int | None = DEFAULT_MAX_ROWS,
) -> Iterator[dict[str, Any]]:
    from app.db import clickhouse_client

    if max_rows is not None and max_rows <= 0:
        raise ValueError("max_rows must be positive when provided")

    client = clickhouse_client()
    cursor = ""
    emitted = 0
    while max_rows is None or emitted < max_rows:
        remaining = None if max_rows is None else max_rows - emitted
        requested = batch_size if remaining is None else min(batch_size, remaining)
        rows = _dict_rows(
            client.query(
                _duration_batch_sql(
                    after_application_number=cursor,
                    batch_size=requested,
                )
            )
        )
        if not rows:
            break
        for row in rows:
            yield row
            emitted += 1
        cursor = str(rows[-1]["application_number"])
        if len(rows) < requested:
            break


def _engine_version() -> str:
    try:
        return metadata.version("markorbit-data-engine")
    except metadata.PackageNotFoundError:
        return "source-tree"


def build_live_materialization(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rows: int | None = DEFAULT_MAX_ROWS,
    generated_at: str | None = None,
) -> DurationDatasetMaterialization:
    coverage_date = _coverage_date()
    generated = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return materialize_duration_dataset(
        iter_live_duration_rows(batch_size=batch_size, max_rows=max_rows),
        engine_version=_engine_version(),
        watermark=f"cn-data-coverage:{coverage_date.isoformat()}",
        generated_at=generated,
        max_rows=max_rows,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize the bounded CN filing-to-preliminary-publication research dataset."
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    args = parser.parse_args()

    materialization = build_live_materialization(
        batch_size=args.batch_size,
        max_rows=args.max_rows,
    )
    print(json.dumps(materialization.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
