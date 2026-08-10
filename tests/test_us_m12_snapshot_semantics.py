from dataclasses import replace
from datetime import date
from pathlib import Path
import uuid

from app.us.parser import iter_case_bundles
from app.us.publisher import TABLE_COLUMNS, bundle_rows
from app.us.publisher_m12 import (
    CURRENT_SNAPSHOT_TABLES,
    SNAPSHOT_CHILD_TABLES,
    SnapshotAwareUSBatchPublisher,
    _text,
)


FIXTURE = Path("tests/fixtures/us_m1_daily.xml")


class QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class FakeClickHouse:
    def __init__(
        self,
        existing: dict[str, list[list[object]]],
        existing_case_dates: dict[str, date] | None = None,
    ) -> None:
        self.existing = existing
        self.existing_case_dates = existing_case_dates or {}
        self.queries: list[str] = []
        self.inserts: list[tuple[str, list[list[object]], list[str]]] = []

    def query(self, sql: str) -> QueryResult:
        self.queries.append(sql)
        if "FROM markorbit_facts.us_case_current FINAL" in sql:
            return QueryResult(
                [(serial, transaction_date) for serial, transaction_date in self.existing_case_dates.items()]
            )
        for table, rows in self.existing.items():
            if f"FROM {table} FINAL" in sql:
                return QueryResult([tuple(row) for row in rows])
        return QueryResult([])

    def insert(self, table: str, rows: list[list[object]], column_names: list[str]) -> None:
        self.inserts.append((table, [list(row) for row in rows], list(column_names)))


def _old_child_rows() -> tuple[object, dict[str, list[list[object]]]]:
    bundle = list(iter_case_bundles(FIXTURE))[0]
    rows = bundle_rows(
        bundle,
        package_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        package_kind="HISTORICAL_APPLICATIONS",
        source_effective_date=date(2025, 12, 31),
        source_file="history.xml",
        source_rank=100,
    )
    existing = {table: rows[table] for table in SNAPSHOT_CHILD_TABLES}
    return bundle, existing


def test_newer_snapshot_tombstones_children_omitted_from_case_snapshot() -> None:
    old_bundle, existing = _old_child_rows()
    new_bundle = replace(old_bundle, owners=(), classifications=(), statements=())
    package_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    client = FakeClickHouse(existing)
    publisher = SnapshotAwareUSBatchPublisher(
        client,
        package_id=package_id,
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 1, 8),
        source_rank=200,
        batch_size=100,
    )

    publisher.add(new_bundle, "apc260108.xml")
    publisher.close()

    for table, key_column in SNAPSHOT_CHILD_TABLES.items():
        columns = TABLE_COLUMNS[table]
        inserted = [rows for name, rows, _columns in client.inserts if name == table]
        if not existing[table]:
            assert inserted == []
            assert publisher.tombstone_counts[table] == 0
            continue
        assert len(inserted) == 1
        tombstones = [row for row in inserted[0] if row[columns.index("is_deleted")] == 1]
        assert len(tombstones) == len(existing[table])
        expected_keys = {row[columns.index(key_column)] for row in existing[table]}
        actual_keys = {row[columns.index(key_column)] for row in tombstones}
        assert actual_keys == expected_keys
        for tombstone in tombstones:
            assert tombstone[columns.index("last_source_package_id")] == package_id
            assert tombstone[columns.index("source_rank")] == 200
            assert tombstone[columns.index("source_file")] == "apc260108.xml"
        assert publisher.tombstone_counts[table] == len(existing[table])


def test_child_still_present_in_new_snapshot_is_not_tombstoned() -> None:
    old_bundle, existing = _old_child_rows()
    client = FakeClickHouse(existing)
    publisher = SnapshotAwareUSBatchPublisher(
        client,
        package_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 1, 8),
        source_rank=200,
        batch_size=100,
    )

    publisher.add(old_bundle, "apc260108.xml")
    publisher.close()

    for table in SNAPSHOT_CHILD_TABLES:
        inserted = [rows for name, rows, _columns in client.inserts if name == table]
        if not existing[table]:
            assert inserted == []
            assert publisher.tombstone_counts[table] == 0
            continue
        columns = TABLE_COLUMNS[table]
        assert len(inserted) == 1
        assert all(row[columns.index("is_deleted")] == 0 for row in inserted[0])
        assert publisher.tombstone_counts[table] == 0


def test_snapshot_lookup_only_considers_older_current_children() -> None:
    old_bundle, existing = _old_child_rows()
    client = FakeClickHouse(existing)
    publisher = SnapshotAwareUSBatchPublisher(
        client,
        package_id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 1, 8),
        source_rank=200,
        batch_size=100,
    )

    publisher.add(replace(old_bundle, owners=(), classifications=(), statements=()), "apc260108.xml")
    publisher.close()

    assert len(client.queries) == len(SNAPSHOT_CHILD_TABLES) + 1
    assert "FROM markorbit_facts.us_case_current FINAL" in client.queries[0]
    child_queries = client.queries[1:]
    assert all("source_rank < 200" in sql for sql in child_queries)
    assert all("is_deleted = 0" in sql for sql in child_queries)


def test_older_transaction_does_not_replace_newer_current_snapshot() -> None:
    old_bundle, existing = _old_child_rows()
    serial = old_bundle.case.serial_number
    incoming = replace(
        old_bundle,
        case=replace(old_bundle.case, transaction_date=date(2026, 1, 8)),
    )
    client = FakeClickHouse(existing, {serial: date(2026, 3, 4)})
    publisher = SnapshotAwareUSBatchPublisher(
        client,
        package_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 1, 8),
        source_rank=3_000_000_000_000_001,
        batch_size=100,
    )

    publisher.add(incoming, "apc260108.xml")
    publisher.close()

    inserted_tables = {name for name, _rows, _columns in client.inserts}
    assert not (inserted_tables & CURRENT_SNAPSHOT_TABLES)
    assert "markorbit_facts.us_case_observation_history" in inserted_tables
    assert all(count == 0 for count in publisher.tombstone_counts.values())


def test_newer_transaction_still_updates_current_snapshot() -> None:
    old_bundle, existing = _old_child_rows()
    serial = old_bundle.case.serial_number
    incoming = replace(
        old_bundle,
        case=replace(old_bundle.case, transaction_date=date(2026, 3, 4)),
    )
    client = FakeClickHouse(existing, {serial: date(2026, 1, 8)})
    publisher = SnapshotAwareUSBatchPublisher(
        client,
        package_id=uuid.UUID("88888888-8888-8888-8888-888888888888"),
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 3, 4),
        source_rank=3_000_000_000_000_002,
        batch_size=100,
    )

    publisher.add(incoming, "apc260304.xml")
    publisher.close()

    case_rows = [
        rows
        for name, rows, _columns in client.inserts
        if name == "markorbit_facts.us_case_current"
    ]
    assert len(case_rows) == 1
    transaction_index = TABLE_COLUMNS["markorbit_facts.us_case_current"].index("transaction_date")
    assert case_rows[0][0][transaction_index] == date(2026, 3, 4)


def test_event_histories_are_not_snapshot_tombstoned() -> None:
    old_bundle, existing = _old_child_rows()
    client = FakeClickHouse(existing)
    publisher = SnapshotAwareUSBatchPublisher(
        client,
        package_id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 1, 8),
        source_rank=200,
        batch_size=100,
    )

    publisher.add(replace(old_bundle, owners=(), classifications=(), statements=()), "apc260108.xml")
    publisher.close()

    assert "markorbit_facts.us_event_history" not in SNAPSHOT_CHILD_TABLES
    assert "markorbit_facts.us_madrid_event_history" not in SNAPSHOT_CHILD_TABLES
    event_columns = TABLE_COLUMNS["markorbit_facts.us_event_history"]
    event_rows = [
        rows
        for name, rows, _columns in client.inserts
        if name == "markorbit_facts.us_event_history"
    ][0]
    assert "is_deleted" not in event_columns
    assert len(event_rows) == len(old_bundle.events)


def test_fixedstring_bytes_are_normalized_before_tombstone_reinsert() -> None:
    old_bundle, existing = _old_child_rows()
    owner_table = "markorbit_facts.us_owner_current"
    owner_columns = TABLE_COLUMNS[owner_table]
    original_owner = list(existing[owner_table][0])
    byte_owner = list(original_owner)
    fixed_string_columns = ("owner_key", "source_row_hash", "record_hash")
    for column in fixed_string_columns:
        index = owner_columns.index(column)
        byte_owner[index] = str(byte_owner[index]).encode("utf-8")
    existing[owner_table] = [byte_owner]

    client = FakeClickHouse(existing)
    publisher = SnapshotAwareUSBatchPublisher(
        client,
        package_id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 1, 8),
        source_rank=200,
        batch_size=100,
    )
    publisher.add(replace(old_bundle, owners=()), "apc260108.xml")
    publisher.close()

    inserted = [rows for name, rows, _columns in client.inserts if name == owner_table]
    assert len(inserted) == 1
    tombstones = [row for row in inserted[0] if row[owner_columns.index("is_deleted")] == 1]
    assert len(tombstones) == 1
    tombstone = tombstones[0]
    assert tombstone[owner_columns.index("owner_key")] == original_owner[
        owner_columns.index("owner_key")
    ]
    assert all(not isinstance(value, bytes) for value in tombstone)


def test_fixedstring_text_normalization_strips_nul_padding() -> None:
    assert _text(b"abc\x00\x00") == "abc"
    assert _text(bytearray(b"abc\x00")) == "abc"
    assert _text(memoryview(b"abc\x00")) == "abc"
