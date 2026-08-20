from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
import hashlib
from typing import Iterable

from app.db import clickhouse_client
from app.us_tsdr.policy import Candidate


_CN_CODES_SQL = "'CN','CHN','CHINA','PRC'"
_NEW_APPLICATION_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class CandidatePool:
    candidates: list[Candidate]
    source_watermark_to: tuple[int, str]
    backfill_bucket: int
    lane_counts: dict[str, int]


def _normalize(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    return value


def _rows(client, sql: str) -> list[dict[str, object]]:
    result = client.query(sql)
    return [
        {name: _normalize(value) for name, value in zip(result.column_names, row, strict=True)}
        for row in result.result_rows
    ]


def _serial(value: object) -> str:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) != 8:
        raise ValueError(f"invalid US serial_number from source: {text!r}")
    return text


def _in_clause(serials: Iterable[str]) -> str:
    values = [_serial(item) for item in serials]
    if not values:
        return "('')"
    return "(" + ",".join(f"'{item}'" for item in values) + ")"


def _load_case_details(client, serials: list[str]) -> list[Candidate]:
    if not serials:
        return []

    cases: dict[str, dict[str, object]] = {}
    owner_countries: dict[str, set[str]] = defaultdict(set)
    attorney_names: dict[str, set[str]] = defaultdict(set)

    for offset in range(0, len(serials), 10_000):
        chunk = serials[offset : offset + 10_000]
        clause = _in_clause(chunk)
        for row in _rows(
            client,
            f"""
            SELECT serial_number, source_rank, filing_date, abandonment_date, cancellation_date
            FROM markorbit_facts.us_case_current FINAL
            WHERE is_deleted = 0 AND serial_number IN {clause}
            """,
        ):
            cases[_serial(row["serial_number"])] = row

        for row in _rows(
            client,
            f"""
            SELECT serial_number, country, nationality_country
            FROM markorbit_facts.us_owner_current FINAL
            WHERE is_deleted = 0 AND serial_number IN {clause}
            """,
        ):
            serial = _serial(row["serial_number"])
            for key in ("country", "nationality_country"):
                value = str(row.get(key) or "").strip().upper()
                if value:
                    owner_countries[serial].add(value)

        for row in _rows(
            client,
            f"""
            SELECT serial_number, attorney_name
            FROM markorbit_facts.us_correspondent_current FINAL
            WHERE is_deleted = 0 AND serial_number IN {clause}
            """,
        ):
            serial = _serial(row["serial_number"])
            name = str(row.get("attorney_name") or "").strip()
            if name:
                attorney_names[serial].add(" ".join(name.casefold().split()))

    result: list[Candidate] = []
    for serial in serials:
        row = cases.get(serial)
        if not row:
            continue
        countries = owner_countries.get(serial, set())
        applicant_country = (
            "CN"
            if countries.intersection({"CN", "CHN", "CHINA", "PRC"})
            else (sorted(countries)[0] if countries else "")
        )
        terminal = row.get("abandonment_date") is not None or row.get("cancellation_date") is not None
        names = sorted(attorney_names.get(serial, set()))
        attorney_fingerprint = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()
        filing_date = row.get("filing_date")
        if filing_date is not None and not isinstance(filing_date, date):
            filing_date = None
        result.append(
            Candidate(
                serial_number=serial,
                source_rank=int(row.get("source_rank") or 0),
                filing_date=filing_date,
                applicant_country=applicant_country,
                current_attorney_present=bool(names),
                source_attorney_fingerprint=attorney_fingerprint,
                lifecycle_state="TERMINAL_INVALID" if terminal else "REFRESHABLE",
            )
        )
    return result


def load_candidate_pool(
    *,
    source_watermark: tuple[int, str],
    capacity: int,
    backfill_bucket: int,
) -> CandidatePool:
    """Build a bounded weekly candidate pool from current US Application facts."""
    if capacity < 1:
        raise ValueError("capacity must be positive")
    rank, watermark_serial = source_watermark
    watermark_serial = (
        watermark_serial if watermark_serial.isdigit() and len(watermark_serial) == 8 else ""
    )
    client = clickhouse_client()

    new_limit = min(max(capacity * 2, 10_000), 600_000)
    new_rows = _rows(
        client,
        f"""
        SELECT serial_number
        FROM markorbit_facts.us_case_current FINAL
        WHERE is_deleted = 0
          AND filing_date >= today() - INTERVAL {_NEW_APPLICATION_LOOKBACK_DAYS} DAY
        ORDER BY filing_date DESC, source_rank DESC, serial_number
        LIMIT {new_limit}
        """,
    )
    new_serials = [_serial(row["serial_number"]) for row in new_rows]

    changed_rows = _rows(
        client,
        f"""
        SELECT serial_number, max(source_rank) AS latest_source_rank
        FROM markorbit_facts.us_correspondent_current FINAL
        WHERE is_deleted = 0
          AND (source_rank > {int(rank)}
               OR (source_rank = {int(rank)} AND serial_number > '{watermark_serial}'))
        GROUP BY serial_number
        ORDER BY latest_source_rank, serial_number
        LIMIT {int(capacity)}
        """,
    )
    changed_serials = [_serial(row["serial_number"]) for row in changed_rows]
    if changed_rows:
        last_changed = changed_rows[-1]
        changed_watermark = (
            int(last_changed.get("latest_source_rank") or rank),
            _serial(last_changed["serial_number"]),
        )
    else:
        changed_watermark = (int(rank), watermark_serial)

    cn_limit = min(max(capacity, 10_000), 300_000)
    cn_rows = _rows(
        client,
        f"""
        SELECT serial_number
        FROM markorbit_facts.us_owner_current FINAL
        WHERE is_deleted = 0
          AND (upperUTF8(trim(country)) IN ({_CN_CODES_SQL})
               OR upperUTF8(trim(nationality_country)) IN ({_CN_CODES_SQL}))
        GROUP BY serial_number
        ORDER BY max(source_rank) DESC, serial_number
        LIMIT {cn_limit}
        """,
    )
    cn_serials = [_serial(row["serial_number"]) for row in cn_rows]

    no_attorney_limit = min(max(capacity // 2, 10_000), 150_000)
    no_attorney_rows = _rows(
        client,
        f"""
        SELECT serial_number
        FROM markorbit_facts.us_correspondent_current FINAL
        WHERE is_deleted = 0
        GROUP BY serial_number
        HAVING countIf(length(trim(attorney_name)) > 0) = 0
        ORDER BY max(source_rank) DESC, serial_number
        LIMIT {no_attorney_limit}
        """,
    )
    no_attorney_serials = [_serial(row["serial_number"]) for row in no_attorney_rows]

    bucket = int(backfill_bucket) % 52
    backfill_limit = min(max(capacity, 10_000), 300_000)
    backfill_rows = _rows(
        client,
        f"""
        SELECT serial_number
        FROM markorbit_facts.us_case_current FINAL
        WHERE is_deleted = 0 AND cityHash64(serial_number) % 52 = {bucket}
        ORDER BY serial_number
        LIMIT {backfill_limit}
        """,
    )
    backfill_serials = [_serial(row["serial_number"]) for row in backfill_rows]

    ordered: list[str] = []
    seen: set[str] = set()
    for value in [*new_serials, *changed_serials, *cn_serials, *no_attorney_serials, *backfill_serials]:
        if value not in seen:
            seen.add(value)
            ordered.append(value)

    details = _load_case_details(client, ordered)
    new_set = set(new_serials)
    candidates = [
        replace(item, is_new_application=item.serial_number in new_set)
        for item in details
    ]
    return CandidatePool(
        candidates=candidates,
        source_watermark_to=changed_watermark,
        backfill_bucket=bucket,
        lane_counts={
            "recent_filing": len(new_serials),
            "correspondence_changed": len(changed_serials),
            "cn_applicant": len(cn_serials),
            "no_current_attorney": len(no_attorney_serials),
            "historical_bucket": len(backfill_serials),
            "deduplicated_candidate_pool": len(candidates),
        },
    )
