from dataclasses import replace
from datetime import date
import uuid

from app.us.change_history import (
    CASE_OBSERVATION_COLUMNS,
    build_case_observation_row,
    derive_change,
    derive_changes,
    owner_snapshot_fingerprints,
)
from app.us.model import USCaseBundle, USCaseRecord, USOwnerRecord


SERIAL = "88991234"


def _owner(name: str, *, address: str = "1 Main St") -> USOwnerRecord:
    return USOwnerRecord(
        serial_number=SERIAL,
        entry_number=1,
        party_type="10",
        legal_entity_type_code="16",
        party_name=name,
        country="US",
        address_1=address,
    )


def _bundle(
    *,
    status_code: str = "630",
    owner: USOwnerRecord | None = None,
    transaction_date: date | None = None,
) -> USCaseBundle:
    return USCaseBundle(
        case=USCaseRecord(
            serial_number=SERIAL,
            registration_number="7654321",
            transaction_date=transaction_date,
            filing_date=date(2025, 1, 2),
            status_code=status_code,
            status_date=date(2026, 1, 2),
            current_location="TMO LAW OFFICE",
            intent_to_use_1b_current=True,
        ),
        owners=(owner or _owner("Alpha Brand LLC"),),
    )


def _observation_dict(row: list[object]) -> dict[str, object]:
    return dict(zip(CASE_OBSERVATION_COLUMNS, row, strict=True))


def test_owner_identity_fingerprint_ignores_address_only_change() -> None:
    first = owner_snapshot_fingerprints((_owner("Alpha Brand LLC", address="1 A St"),))
    second = owner_snapshot_fingerprints((_owner("Alpha Brand LLC", address="2 B St"),))
    assert first["owner_set_hash"] == second["owner_set_hash"]
    assert first["owner_record_set_hash"] != second["owner_record_set_hash"]


def test_owner_identity_fingerprint_detects_name_change() -> None:
    first = owner_snapshot_fingerprints((_owner("Alpha Brand LLC"),))
    second = owner_snapshot_fingerprints((_owner("Beta Brand Inc."),))
    assert first["owner_set_hash"] != second["owner_set_hash"]


def test_observation_row_has_deterministic_lineage_and_owner_snapshot() -> None:
    package_id = uuid.UUID("00000000-0000-0000-0000-000000000123")
    row = build_case_observation_row(
        _bundle(),
        package_id=package_id,
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 1, 2),
        source_file="apc260102.xml",
        source_rank=202601020000000001,
    )
    payload = _observation_dict(row)
    assert payload["serial_number"] == SERIAL
    assert payload["source_package_id"] == package_id
    assert payload["owner_count"] == 1
    assert payload["owner_names"] == ["Alpha Brand LLC"]
    assert len(str(payload["observation_key"])) == 64
    assert len(str(payload["observation_hash"])) == 64


def test_derive_change_separates_status_and_owner_identity() -> None:
    first = _observation_dict(
        build_case_observation_row(
            _bundle(status_code="630", owner=_owner("Alpha Brand LLC")),
            package_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 1, 1),
            source_file="apc260101.xml",
            source_rank=100,
        )
    )
    second = _observation_dict(
        build_case_observation_row(
            _bundle(status_code="700", owner=_owner("Beta Brand Inc.")),
            package_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 1, 2),
            source_file="apc260102.xml",
            source_rank=200,
        )
    )
    change = derive_change(first, second)
    assert change is not None
    assert "STATUS_CODE_CHANGED" in change["change_types"]
    assert "OWNER_IDENTITY_SET_CHANGED" in change["change_types"]
    assert change["field_changes"]["owners"] == {
        "before": ["Alpha Brand LLC"],
        "after": ["Beta Brand Inc."],
    }
    assert change["legal_status_inference"] is False


def test_derive_change_marks_owner_details_without_false_owner_change() -> None:
    first_bundle = _bundle(owner=_owner("Alpha Brand LLC", address="1 A St"))
    second_bundle = replace(
        first_bundle,
        owners=(_owner("Alpha Brand LLC", address="2 B St"),),
    )
    first = _observation_dict(
        build_case_observation_row(
            first_bundle,
            package_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 1, 1),
            source_file="apc260101.xml",
            source_rank=300,
        )
    )
    second = _observation_dict(
        build_case_observation_row(
            second_bundle,
            package_id=uuid.UUID("00000000-0000-0000-0000-000000000004"),
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 1, 2),
            source_file="apc260102.xml",
            source_rank=400,
        )
    )
    change = derive_change(first, second)
    assert change is not None
    assert "OWNER_DETAILS_CHANGED" in change["change_types"]
    assert "OWNER_IDENTITY_SET_CHANGED" not in change["change_types"]


def test_derive_changes_skips_unchanged_observation() -> None:
    bundle = _bundle()
    rows = [
        _observation_dict(
            build_case_observation_row(
                bundle,
                package_id=uuid.UUID(f"00000000-0000-0000-0000-{index:012d}"),
                package_kind="DAILY_APPLICATIONS",
                source_effective_date=date(2026, 1, index),
                source_file=f"apc26010{index}.xml",
                source_rank=index,
            )
        )
        for index in (1, 2)
    ]
    assert derive_changes(rows) == []


def test_derive_changes_orders_real_freshness_before_package_rank() -> None:
    historical_newer = _observation_dict(
        build_case_observation_row(
            _bundle(status_code="700", transaction_date=date(2026, 3, 4)),
            package_id=uuid.UUID("00000000-0000-0000-0000-000000000010"),
            package_kind="HISTORICAL_APPLICATIONS",
            source_effective_date=date(2025, 12, 31),
            source_file="apc18840407-20251231-05.xml",
            source_rank=1_000_000_000_000_005,
        )
    )
    daily_older = _observation_dict(
        build_case_observation_row(
            _bundle(status_code="630", transaction_date=date(2026, 1, 8)),
            package_id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 1, 8),
            source_file="apc260108.xml",
            source_rank=3_000_000_000_000_001,
        )
    )

    changes = derive_changes([historical_newer, daily_older])

    assert len(changes) == 1
    assert changes[0]["field_changes"]["status_code"] == {
        "before": "630",
        "after": "700",
    }
    assert changes[0]["observation_key"] == historical_newer["observation_key"]
