from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile

from app.us.maintenance import calculate_maintenance_schedule
from app.us.reference_pack import build_reference_pack


USPTO_XML_URL = (
    "https://www.uspto.gov/trademarks/trademark-updates-and-announcements/"
    "xml-resources"
)


def _find_obligation(report: dict, code: str, term_text: str | None = None) -> dict:
    for row in report["obligations"]:
        if row["code"] != code:
            continue
        if term_text is not None and term_text not in row["label"]:
            continue
        return row
    raise RuntimeError(f"Missing obligation {code} {term_text or ''}: {report}")


def main() -> None:
    non_madrid = calculate_maintenance_schedule(
        registration_date=date(2020, 7, 28),
        as_of=date(2026, 8, 9),
    )
    section_8 = _find_obligation(non_madrid, "SECTION_8_FIRST")
    if section_8["state_as_of"] != "OPEN_GRACE":
        raise RuntimeError(f"Expected Section 8 grace state: {section_8}")

    leap = calculate_maintenance_schedule(
        registration_date=date(2020, 2, 29),
        as_of=date(2026, 8, 9),
    )
    leap_first = _find_obligation(leap, "SECTION_8_FIRST")
    if leap_first["nominal_regular_deadline"] != date(2026, 2, 28):
        raise RuntimeError(f"Leap-day anniversary clamp failed: {leap_first}")

    madrid = calculate_maintenance_schedule(
        registration_date=date(2017, 6, 15),
        as_of=date(2026, 8, 9),
        madrid_66a=True,
        international_registration_date=date(2016, 3, 1),
    )
    section_71 = _find_obligation(madrid, "SECTION_71_DECENNIAL", "year 10")
    if section_71["state_as_of"] != "OPEN_REGULAR":
        raise RuntimeError(f"Expected Section 71 regular window: {section_71}")
    if not madrid["external_reminders"]:
        raise RuntimeError("Madrid fixture did not emit WIPO renewal reminders")

    legacy = calculate_maintenance_schedule(
        registration_date=date(1988, 1, 1),
        as_of=date(2026, 8, 9),
    )
    if legacy["mode"] != "LEGACY_TERM_REQUIRES_RENEWAL_HISTORY":
        raise RuntimeError(f"Legacy registration did not fail safe: {legacy}")
    if legacy["obligations"]:
        raise RuntimeError(f"Legacy registration guessed obligations: {legacy}")

    section_15 = non_madrid["optional_filings"][0]
    if section_15["eligibility"] != "REQUIRES_EXTERNAL_FACTS":
        raise RuntimeError(f"Section 15 was over-inferred: {section_15}")

    with tempfile.TemporaryDirectory(prefix="markorbit-us-reference-pack-") as temp_dir:
        root = Path(temp_dir)
        source = root / "Table1TrademarkStatusCodes_20250813.doc"
        reviewed = root / "reviewed_status_codes.csv"
        source.write_bytes(b"ci-fixture-official-source-bytes")
        reviewed.write_text(
            "code,official_description,official_definition,official_category,source_locator\n"
            "700,CI fixture status,,,Table 1 fixture row\n",
            encoding="utf-8",
        )
        pack = build_reference_pack(
            family="status",
            source_document=source,
            reviewed_csv=reviewed,
            reference_version="USPTO_STATUS_PACK_CI_V1",
            document_date=date(2025, 8, 13),
            source_url=USPTO_XML_URL,
            evidence_note="CI fixture only",
        )
        manifest = pack["manifest"]
        if manifest["record_count"] != 1:
            raise RuntimeError(f"Reference pack row count mismatch: {manifest}")
        if pack["source_evidence"]["source_document_sha256"] != manifest[
            "source_document_sha256"
        ]:
            raise RuntimeError(f"Reference pack source evidence mismatch: {pack}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "contract": "US_MAINTENANCE_AND_REFERENCE_PACK_FIXTURE",
                "non_madrid_section_8_grace": "PASS",
                "leap_day_calendar_math": "PASS",
                "madrid_section_71": "PASS",
                "wipo_external_reminder": "PASS",
                "section_15_external_facts_only": "PASS",
                "legacy_fail_safe": "PASS",
                "reference_pack_source_and_csv_hashes": "PASS",
                "legal_status_inference": "NONE",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
