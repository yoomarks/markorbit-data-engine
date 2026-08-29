from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.cn.discovery_preliminary_publication import (
    PreliminaryPublicationDiscoveryRequest,
    execute_page,
)
from app.db import clickhouse_client

ACCEPTANCE_VERSION = "CN_PRELIMINARY_PUBLICATION_DISCOVERY_ACCEPTANCE_V1"


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _page_evidence(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_hash": page["query"]["query_hash"],
        "snapshot_id": page["snapshot"]["snapshot_id"],
        "page_number": page["provenance"]["page_number"],
        "result_count": page["provenance"]["result_count"],
        "emitted_count": page["provenance"]["emitted_count"],
        "has_more": page["provenance"]["has_more"],
        "next_cursor_hash": (
            hashlib.sha256(page["next_cursor"].encode("ascii")).hexdigest()
            if page["next_cursor"]
            else None
        ),
        "result_hash": _canonical_hash(page["results"]),
        "bounded_truncation": page["bounded_truncation"],
    }


def build_live_acceptance(
    *,
    start_date: date,
    end_date: date,
    page_size: int = 2,
    require_second_page: bool = True,
    client: Any | None = None,
) -> dict[str, Any]:
    db = client or clickhouse_client()
    first_request = PreliminaryPublicationDiscoveryRequest(
        start_date=start_date,
        end_date=end_date,
        page_size=page_size,
    )

    first_a = execute_page(first_request, client=db)
    first_b = execute_page(first_request, client=db)
    first_replay_match = first_a == first_b
    if not first_replay_match:
        raise RuntimeError("Discovery page 1 replay mismatch")
    if not first_a["results"]:
        raise RuntimeError("Discovery acceptance requires at least one real result")

    second_evidence: dict[str, Any] | None = None
    second_replay_match: bool | None = None
    cursor = first_a["next_cursor"]
    if cursor is None:
        if require_second_page:
            raise RuntimeError(
                "Discovery acceptance requires a second page; choose a populated interval or smaller page"
            )
    else:
        second_request = PreliminaryPublicationDiscoveryRequest(
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
            cursor=cursor,
        )
        second_a = execute_page(second_request, client=db)
        second_b = execute_page(second_request, client=db)
        second_replay_match = second_a == second_b
        if not second_replay_match:
            raise RuntimeError("Discovery page 2 replay mismatch")
        first_keys = {
            (item["prelim_pub_date"], item["application_number"], item["case_id"])
            for item in first_a["results"]
        }
        second_keys = {
            (item["prelim_pub_date"], item["application_number"], item["case_id"])
            for item in second_a["results"]
        }
        if first_keys & second_keys:
            raise RuntimeError("Discovery continuation duplicated candidate keys across pages")
        if second_a["snapshot"]["snapshot_id"] != first_a["snapshot"]["snapshot_id"]:
            raise RuntimeError("Discovery continuation crossed serving snapshots")
        if second_a["query"]["query_hash"] != first_a["query"]["query_hash"]:
            raise RuntimeError("Discovery continuation changed query identity")
        second_evidence = _page_evidence(second_a)

    return {
        "acceptance_version": ACCEPTANCE_VERSION,
        "status": "PASS",
        "read_only": True,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "page_size": page_size,
        "page1_replay_match": first_replay_match,
        "page2_replay_match": second_replay_match,
        "page1": _page_evidence(first_a),
        "page2": second_evidence,
        "query_hash": first_a["query"]["query_hash"],
        "snapshot": first_a["snapshot"],
        "candidate_type": first_a["candidate_type"],
        "no_write_path": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a small read-only live acceptance for CN preliminary-publication Discovery."
    )
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--page-size", type=int, default=2)
    parser.add_argument(
        "--allow-single-page",
        action="store_true",
        help="Permit acceptance when the bounded interval has no continuation page.",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    receipt = build_live_acceptance(
        start_date=args.start_date,
        end_date=args.end_date,
        page_size=args.page_size,
        require_second_page=not args.allow_single_page,
    )
    rendered = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
