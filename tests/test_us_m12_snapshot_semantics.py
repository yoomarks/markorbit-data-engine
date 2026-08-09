from dataclasses import replace
from datetime import date
from pathlib import Path
import uuid

from app.us.parser import iter_case_bundles
from app.us.publisher import TABLE_COLUMNS, bundle_rows
from app.us.publisher_m12 import SNAPSHOT_CHILD_TABLES, SnapshotAwareUSBatchPublisher


FIXTURE = Path("tests/fixtures/us_m1_daily.xml")


class QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class FakeClickHouse:
    def __init__(self, existing: dict[str, list[list[object]]]) -> None:
        self.existing = existing
        self.queries: list[str] = []
        self.inserts: list[tuple[str, list[list[object]], list[str]]] = []

    def query(self, sql: str) -> QueryResult:
        self.queries.append(sql)
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
        assert len(inserted) == 1
        tombstones = [row for row in inserted[0] if row[columns.index("is_deleted")] == 1]
        assert len(tombstones) == 1
        tombstone = tombstones[0]
        assert tombstone[columns.index(key_column)] == existing[table][0][columns.index(key_column)]
        assert tombstone[columns.index("last_source_package_id")] == package_id
        assert tombstone[columns.index("source_rank")] == 200
        assert tombstone[columns.index("source_file")] == "apc260108.xml"
        assert publisher.tombstone_counts[table] == 1


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
        columns = TABLE_COLUMNS[table]
        rows = [rows for name, rows, _columns in client.inserts if name == table][0]
        assert all(row[columns.index("is_deleted")] == 0 for row in rows)
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

    assert len(client.queries) == 3
    assert all("source_rank < 200" in sql for sql in client.queries)
    assert all("is_deleted = 0" in sql for sql in client.queries)


def test_event_history_is_not_snapshot_tombstoned() -> None:
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
    event_columns = TABLE_COLUMNS["markorbit_facts.us_event_history"]
    event_rows = [
        rows
        for name, rows, _columns in client.inserts
        if name == "markorbit_facts.us_event_history"
    ][0]
    assert "is_deleted" not in event_columns
    assert len(event_rows) == len(old_bundle.events)
