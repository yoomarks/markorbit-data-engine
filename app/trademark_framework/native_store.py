from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from psycopg.types.json import Jsonb

from app.trademark_framework.contracts import ObservationDomain


NATIVE_STORE_PRIMITIVES_VERSION = "TRADEMARK_NATIVE_STORE_PRIMITIVES_V1"
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_STANDARD_COLUMNS = frozenset(
    {
        "source_row_hash",
        "record_key",
        "source_object_id",
        "source_index",
        "parser_version",
        "mapping_version",
        "source_payload",
        "observed_at",
    }
)


class NativeSqlType(StrEnum):
    TEXT = "text"
    SMALLINT = "smallint"
    INTEGER = "integer"
    BIGINT = "bigint"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMPTZ = "timestamptz"
    NUMERIC = "numeric"
    JSONB = "jsonb"
    TEXT_ARRAY = "text[]"


@dataclass(frozen=True, slots=True)
class NativeColumn:
    name: str
    data_type: NativeSqlType
    nullable: bool = True

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not _IDENTIFIER_RE.fullmatch(self.name):
            errors.append(f"invalid native column identifier: {self.name!r}")
        if self.name in _STANDARD_COLUMNS:
            errors.append(f"native column collides with provenance column: {self.name}")
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class ObservationTableSpec:
    """Reusable DDL contract for one source-native append-only observation table.

    Only provenance mechanics are standardized. Jurisdictions choose the table name and
    native columns; current-state projection remains a separate country/source contract.
    """

    schema_name: str
    table_name: str
    domain: ObservationDomain
    native_columns: tuple[NativeColumn, ...]

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not _IDENTIFIER_RE.fullmatch(self.schema_name):
            errors.append(f"invalid schema identifier: {self.schema_name!r}")
        if not _IDENTIFIER_RE.fullmatch(self.table_name):
            errors.append(f"invalid table identifier: {self.table_name!r}")
        names = [column.name for column in self.native_columns]
        if len(set(names)) != len(names):
            errors.append("native column names must be unique")
        for column in self.native_columns:
            errors.extend(column.validate())
        return tuple(errors)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def create_statements(self) -> tuple[str, ...]:
        errors = self.validate()
        if errors:
            raise ValueError("; ".join(errors))

        native = [
            f"    {column.name} {column.data_type.value}"
            + ("" if column.nullable else " NOT NULL")
            for column in self.native_columns
        ]
        columns = [
            "    source_row_hash text PRIMARY KEY",
            "    record_key text NOT NULL",
            "    source_object_id uuid NOT NULL REFERENCES acquisition.global_trademark_source_object(object_id)",
            "    source_index integer NOT NULL CHECK (source_index >= 1)",
            "    parser_version text NOT NULL DEFAULT ''",
            "    mapping_version text NOT NULL DEFAULT ''",
            *native,
            "    source_payload jsonb NOT NULL DEFAULT '{}'::jsonb",
            "    observed_at timestamptz NOT NULL DEFAULT now()",
            "    UNIQUE (source_object_id, record_key, source_index, parser_version, mapping_version)",
        ]
        table_sql = (
            f"CREATE TABLE IF NOT EXISTS {self.qualified_name} (\n"
            + ",\n".join(columns)
            + "\n)"
        )
        record_index = _index_name(self.table_name, "record_source")
        source_index = _index_name(self.table_name, "source_object")
        return (
            f"CREATE SCHEMA IF NOT EXISTS {self.schema_name}",
            table_sql,
            (
                f"CREATE INDEX IF NOT EXISTS {record_index} ON {self.qualified_name} "
                "(record_key, source_object_id, source_index)"
            ),
            (
                f"CREATE INDEX IF NOT EXISTS {source_index} ON {self.qualified_name} "
                "(source_object_id)"
            ),
        )


@dataclass(frozen=True, slots=True)
class ObservationRow:
    record_key: str
    source_object_id: uuid.UUID
    source_index: int
    native_values: Mapping[str, object]
    source_payload: Mapping[str, object]
    parser_version: str = ""
    mapping_version: str = ""


def _index_name(table_name: str, suffix: str) -> str:
    raw = f"idx_{table_name}_{suffix}"
    if len(raw) <= 63:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{raw[:52]}_{digest}"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _jsonb(value: object) -> Jsonb:
    # Normalize dates/UUIDs/other source-native scalar wrappers through the same
    # deterministic JSON representation used by the source-row hash.
    return Jsonb(json.loads(_canonical_json(value)))


def observation_row_hash(spec: ObservationTableSpec, row: ObservationRow) -> str:
    """Return deterministic identity for one immutable mapped source observation."""
    material = {
        "domain": spec.domain.value,
        "source_object_id": str(row.source_object_id),
        "record_key": row.record_key,
        "source_index": row.source_index,
        "parser_version": row.parser_version,
        "mapping_version": row.mapping_version,
        "native_values": dict(row.native_values),
        "source_payload": dict(row.source_payload),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def install_observation_table(cur, spec: ObservationTableSpec) -> None:
    """Execute explicit additive DDL for one observation table in caller transaction."""
    for statement in spec.create_statements():
        cur.execute(statement)


def _validated_native_values(
    spec: ObservationTableSpec,
    row: ObservationRow,
) -> tuple[tuple[NativeColumn, ...], tuple[object, ...]]:
    errors = spec.validate()
    if errors:
        raise ValueError("; ".join(errors))
    if not row.record_key.strip():
        raise ValueError("record_key must not be blank")
    if row.source_index < 1:
        raise ValueError("source_index must be >= 1")

    expected = {column.name for column in spec.native_columns}
    actual = set(row.native_values)
    unknown = sorted(actual - expected)
    if unknown:
        raise ValueError("unknown native observation columns: " + ", ".join(unknown))

    values: list[object] = []
    for column in spec.native_columns:
        value = row.native_values.get(column.name)
        if value is None and not column.nullable:
            raise ValueError(f"required native observation column missing: {column.name}")
        if value is not None and column.data_type == NativeSqlType.JSONB:
            value = _jsonb(value)
        values.append(value)
    return spec.native_columns, tuple(values)


def append_observation(cur, spec: ObservationTableSpec, row: ObservationRow) -> bool:
    """Append one immutable observation and return whether a row was newly inserted.

    Replay of the same source object/record/index/parser/mapping/payload is idempotent.
    If the same source position under the same parser/mapping produces different mapped
    evidence, execution fails closed instead of storing two contradictory observations.
    A reviewed parser/mapping version can intentionally produce separate evidence.
    """
    columns, native_values = _validated_native_values(spec, row)
    source_row_hash = observation_row_hash(spec, row)
    column_names = [
        "source_row_hash",
        "record_key",
        "source_object_id",
        "source_index",
        "parser_version",
        "mapping_version",
        *(column.name for column in columns),
        "source_payload",
    ]
    placeholders = ", ".join("%s" for _ in column_names)
    values = (
        source_row_hash,
        row.record_key,
        row.source_object_id,
        row.source_index,
        row.parser_version,
        row.mapping_version,
        *native_values,
        _jsonb(dict(row.source_payload)),
    )
    cur.execute(
        f"INSERT INTO {spec.qualified_name} ({', '.join(column_names)}) "
        f"VALUES ({placeholders}) "
        "ON CONFLICT (source_object_id, record_key, source_index, parser_version, mapping_version) "
        "DO NOTHING RETURNING source_row_hash",
        values,
    )
    inserted = cur.fetchone()
    if inserted is not None:
        return True

    cur.execute(
        f"SELECT source_row_hash FROM {spec.qualified_name} "
        "WHERE source_object_id = %s AND record_key = %s AND source_index = %s "
        "AND parser_version = %s AND mapping_version = %s",
        (
            row.source_object_id,
            row.record_key,
            row.source_index,
            row.parser_version,
            row.mapping_version,
        ),
    )
    existing = cur.fetchone()
    if existing and existing["source_row_hash"] == source_row_hash:
        return False
    raise RuntimeError(
        "nondeterministic native observation replay: identical source position and "
        "parser/mapping lineage produced different evidence"
    )
