from __future__ import annotations

import json

from app.db import clickhouse_client
from app.us.status_reference import active_reference_metadata, enrich_status_counts


def current_status_counts() -> list[dict[str, object]]:
    rows = clickhouse_client().query(
        """
        SELECT status_code, count() AS case_count
        FROM markorbit_facts.us_case_current FINAL
        WHERE is_deleted = 0
        GROUP BY status_code
        ORDER BY case_count DESC, status_code
        """
    ).result_rows
    return [
        {"status_code": str(status_code), "case_count": int(case_count)}
        for status_code, case_count in rows
    ]


def build_inventory() -> dict[str, object]:
    counts = current_status_counts()
    enrichment = enrich_status_counts(counts)
    metadata = active_reference_metadata()
    return {
        "semantics": "USPTO_OFFICIAL_REFERENCE_NOT_MARKORBIT_LEGAL_CONCLUSION",
        "reference": metadata,
        "reference_record_count": int(metadata["record_count"]) if metadata else 0,
        "observed_status_code_count": len(counts),
        "observed_case_count": sum(int(row["case_count"]) for row in counts),
        "mapped_code_count": enrichment["mapped_code_count"],
        "unmapped_code_count": enrichment["unmapped_code_count"],
        "unmapped_status_codes": enrichment["unmapped_status_codes"],
        "status_codes": enrichment["status_codes"],
    }


def main() -> None:
    print(json.dumps(build_inventory(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
