from __future__ import annotations

import json

from app.db import clickhouse_client
from app.us.event_reference import list_active_event_codes, lookup_active_event_codes


def current_event_counts() -> list[dict[str, object]]:
    rows = clickhouse_client().query(
        """
        SELECT event_code, count() AS event_count
        FROM markorbit_facts.us_event_history FINAL
        WHERE event_code != ''
        GROUP BY event_code
        ORDER BY event_count DESC, event_code
        """
    ).result_rows
    return [
        {"event_code": str(event_code), "event_count": int(event_count)}
        for event_code, event_count in rows
    ]


def build_inventory() -> dict[str, object]:
    counts = current_event_counts()
    lookup = lookup_active_event_codes([str(row["event_code"]) for row in counts])
    mappings = lookup["mappings"]
    rows: list[dict[str, object]] = []
    unmapped: list[dict[str, object]] = []
    for row in counts:
        code = str(row["event_code"])
        mapping = mappings.get(code)
        item = {**row, "official_event_reference": mapping}
        rows.append(item)
        if mapping is None:
            unmapped.append(dict(row))

    active = list_active_event_codes()
    return {
        "semantics": "USPTO_OFFICIAL_REFERENCE_NOT_MARKORBIT_LEGAL_CONCLUSION",
        "reference": active["reference"],
        "reference_record_count": len(active["event_codes"]),
        "observed_event_code_count": len(counts),
        "observed_event_count": sum(int(row["event_count"]) for row in counts),
        "mapped_code_count": len(counts) - len(unmapped),
        "unmapped_code_count": len(unmapped),
        "unmapped_event_codes": unmapped,
        "event_codes": rows,
    }


def main() -> None:
    print(json.dumps(build_inventory(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
