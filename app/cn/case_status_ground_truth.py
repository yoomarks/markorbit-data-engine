from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


REVIEW_PACKET_VERSION = "CN_CASE_STATUS_REVIEW_PACKET_V1"
EXPECTED_AUDIT_NAME = "CN_CASE_STATUS_INFERENCE_HISTORICAL_VALIDATION"

LABEL_NOT_REVIEWED = "NOT_REVIEWED"
LABEL_CONFIRMED = "CONFIRMED"
LABEL_REJECTED = "REJECTED"
LABEL_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
ALLOWED_LABELS = {
    LABEL_NOT_REVIEWED,
    LABEL_CONFIRMED,
    LABEL_REJECTED,
    LABEL_INSUFFICIENT,
}
DECISIVE_LABELS = {LABEL_CONFIRMED, LABEL_REJECTED}

REVIEW_COLUMNS = [
    "review_packet_version",
    "model_version",
    "audit_version",
    "coverage_date",
    "as_of_date",
    "review_id",
    "application_number",
    "rule_id",
    "inferred_status",
    "inferred_cause",
    "inferred_scope",
    "confidence_score",
    "filing_date",
    "prelim_pub_date",
    "registration_pub_date",
    "first_final_inactive_date",
    "total_final_inactive_date",
    "known_item_count",
    "final_inactive_item_count",
    "review_label",
    "official_status",
    "official_cause",
    "official_event_date",
    "official_source_ref",
    "reviewer",
    "reviewed_at",
    "notes",
]


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _rule_sort_key(rule_id: str) -> tuple[int, str]:
    text = str(rule_id).strip().upper()
    if text.startswith("R") and text[1:].isdigit():
        return int(text[1:]), text
    return 10_000, text


def _review_id(
    *,
    model_version: str,
    coverage_date: str,
    application_number: str,
    rule_id: str,
    inferred_cause: str,
) -> str:
    payload = "|".join(
        (
            model_version,
            coverage_date,
            application_number,
            rule_id,
            inferred_cause,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_review_rows(audit: dict[str, Any]) -> list[dict[str, str]]:
    """Convert an empirical historical audit into a deterministic review sheet.

    The review packet is deliberately file-based. It never writes reviewer labels
    or inferred legal conclusions into the official fact tables.
    """

    if audit.get("audit") != EXPECTED_AUDIT_NAME:
        raise ValueError(
            f"unsupported audit: expected {EXPECTED_AUDIT_NAME}, got {audit.get('audit')!r}"
        )

    model_version = _text(audit.get("model_version"))
    audit_version = _text(audit.get("audit_version"))
    data_clock = audit.get("data_clock") or {}
    coverage_date = _text(data_clock.get("coverage_date"))
    as_of_date = _text(data_clock.get("as_of_date"))
    if not model_version or not audit_version or not coverage_date or not as_of_date:
        raise ValueError("audit is missing model/audit version or data-clock metadata")

    samples_by_rule = audit.get("samples_by_rule") or {}
    if not isinstance(samples_by_rule, dict):
        raise ValueError("samples_by_rule must be an object keyed by rule id")

    rows: list[dict[str, str]] = []
    seen_review_ids: set[str] = set()
    for rule_id in sorted(samples_by_rule, key=_rule_sort_key):
        samples = samples_by_rule[rule_id]
        if not isinstance(samples, list):
            raise ValueError(f"samples_by_rule[{rule_id!r}] must be a list")
        for sample in samples:
            if not isinstance(sample, dict):
                raise ValueError(f"sample under {rule_id!r} must be an object")
            application_number = _text(sample.get("application_number")).strip()
            inferred_cause = _text(sample.get("inferred_cause")).strip()
            inferred_status = _text(sample.get("inferred_status")).strip()
            if not application_number or not inferred_cause or not inferred_status:
                raise ValueError(
                    f"sample under {rule_id!r} is missing application/status/cause"
                )
            review_id = _review_id(
                model_version=model_version,
                coverage_date=coverage_date,
                application_number=application_number,
                rule_id=str(rule_id),
                inferred_cause=inferred_cause,
            )
            if review_id in seen_review_ids:
                continue
            seen_review_ids.add(review_id)

            row = {column: "" for column in REVIEW_COLUMNS}
            row.update(
                {
                    "review_packet_version": REVIEW_PACKET_VERSION,
                    "model_version": model_version,
                    "audit_version": audit_version,
                    "coverage_date": coverage_date,
                    "as_of_date": as_of_date,
                    "review_id": review_id,
                    "application_number": application_number,
                    "rule_id": str(rule_id),
                    "inferred_status": inferred_status,
                    "inferred_cause": inferred_cause,
                    "inferred_scope": _text(sample.get("inferred_scope")),
                    "confidence_score": _text(sample.get("confidence_score")),
                    "filing_date": _text(sample.get("filing_date")),
                    "prelim_pub_date": _text(sample.get("prelim_pub_date")),
                    "registration_pub_date": _text(sample.get("registration_pub_date")),
                    "first_final_inactive_date": _text(
                        sample.get("first_final_inactive_date")
                    ),
                    "total_final_inactive_date": _text(
                        sample.get("total_final_inactive_date")
                    ),
                    "known_item_count": _text(sample.get("known_item_count")),
                    "final_inactive_item_count": _text(
                        sample.get("final_inactive_item_count")
                    ),
                    "review_label": LABEL_NOT_REVIEWED,
                }
            )
            rows.append(row)

    rows.sort(
        key=lambda row: (
            _rule_sort_key(row["rule_id"]),
            row["application_number"],
            row["review_id"],
        )
    )
    return rows


def write_review_csv(path: Path | str, rows: Iterable[dict[str, Any]]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for source_row in rows:
            writer.writerow({column: _text(source_row.get(column)) for column in REVIEW_COLUMNS})
    return output


def read_review_csv(path: Path | str) -> list[dict[str, str]]:
    source = Path(path)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("review CSV has no header")
        missing = [column for column in REVIEW_COLUMNS if column not in reader.fieldnames]
        if missing:
            raise ValueError(f"review CSV is missing columns: {', '.join(missing)}")
        return [dict(row) for row in reader]


def _normalized_label(value: str | None) -> str:
    label = (value or "").strip().upper()
    return label or LABEL_NOT_REVIEWED


def _score_group(rows: list[dict[str, str]]) -> dict[str, Any]:
    labels = Counter(_normalized_label(row.get("review_label")) for row in rows)
    confirmed = labels[LABEL_CONFIRMED]
    rejected = labels[LABEL_REJECTED]
    decisive = confirmed + rejected
    reviewed = decisive + labels[LABEL_INSUFFICIENT]
    return {
        "rows": len(rows),
        "reviewed": reviewed,
        "unreviewed": labels[LABEL_NOT_REVIEWED],
        "decisive": decisive,
        "confirmed": confirmed,
        "rejected": rejected,
        "insufficient_evidence": labels[LABEL_INSUFFICIENT],
        "precision": (confirmed / decisive) if decisive else None,
        "review_coverage": (reviewed / len(rows)) if rows else None,
    }


def score_review_rows(rows: Iterable[dict[str, str]]) -> dict[str, Any]:
    materialized = list(rows)
    seen_ids: set[str] = set()
    per_rule_rows: dict[str, list[dict[str, str]]] = defaultdict(list)

    for index, row in enumerate(materialized, start=2):
        review_id = (row.get("review_id") or "").strip()
        if not review_id:
            raise ValueError(f"row {index}: missing review_id")
        if review_id in seen_ids:
            raise ValueError(f"row {index}: duplicate review_id {review_id}")
        seen_ids.add(review_id)

        if (row.get("review_packet_version") or "").strip() != REVIEW_PACKET_VERSION:
            raise ValueError(f"row {index}: unsupported review_packet_version")
        label = _normalized_label(row.get("review_label"))
        if label not in ALLOWED_LABELS:
            raise ValueError(f"row {index}: invalid review_label {label!r}")
        if label in DECISIVE_LABELS and not (row.get("official_source_ref") or "").strip():
            raise ValueError(
                f"row {index}: {label} requires official_source_ref for auditable ground truth"
            )

        rule_id = (row.get("rule_id") or "").strip()
        if not rule_id:
            raise ValueError(f"row {index}: missing rule_id")
        per_rule_rows[rule_id].append(row)

    per_rule = {
        rule_id: _score_group(per_rule_rows[rule_id])
        for rule_id in sorted(per_rule_rows, key=_rule_sort_key)
    }
    rules_without_decisive = [
        rule_id for rule_id, metrics in per_rule.items() if metrics["decisive"] == 0
    ]

    versions = sorted(
        {
            (row.get("model_version") or "").strip()
            for row in materialized
            if (row.get("model_version") or "").strip()
        }
    )
    if len(versions) > 1:
        raise ValueError(f"review packet mixes multiple model versions: {versions}")

    return {
        "status": "PASS",
        "review_packet_version": REVIEW_PACKET_VERSION,
        "model_version": versions[0] if versions else "",
        "overall": _score_group(materialized),
        "per_rule": per_rule,
        "ground_truth_readiness": {
            "rules_with_decisive_ground_truth": len(per_rule) - len(rules_without_decisive),
            "rules_without_decisive_ground_truth": rules_without_decisive,
            "decision": "MANUAL_MODEL_REVIEW_REQUIRED",
        },
        "interpretation": (
            "Precision is CONFIRMED / (CONFIRMED + REJECTED). "
            "INSUFFICIENT_EVIDENCE is excluded from precision and retained as an evidence-gap signal. "
            "No score automatically promotes an EMPIRICAL rule."
        ),
    }


def build_packet_file(audit_json: Path | str, output_csv: Path | str) -> Path:
    with Path(audit_json).open("r", encoding="utf-8-sig") as handle:
        audit = json.load(handle)
    rows = build_review_rows(audit)
    return write_review_csv(output_csv, rows)


def score_packet_file(review_csv: Path | str, output_json: Path | str) -> Path:
    result = score_review_rows(read_review_csv(review_csv))
    output = Path(output_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build and score manual ground-truth review packets for CN status inference."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a UTF-8 CSV review packet from audit JSON")
    build.add_argument("audit_json", type=Path)
    build.add_argument("output_csv", type=Path)

    score = subparsers.add_parser("score", help="Score a completed review packet")
    score.add_argument("review_csv", type=Path)
    score.add_argument("output_json", type=Path)

    args = parser.parse_args()
    if args.command == "build":
        output = build_packet_file(args.audit_json, args.output_csv)
    else:
        output = score_packet_file(args.review_csv, args.output_json)
    print(output)


if __name__ == "__main__":
    main()
