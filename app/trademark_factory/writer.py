from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any

from app.trademark_factory.mapping import MappingContract, extract_declared_value
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import (
    ObservationRow,
    ObservationTableSpec,
    append_observation,
)


MAPPED_OBSERVATION_WRITER_VERSION = "TRADEMARK_MAPPED_OBSERVATION_WRITER_V1"
Transform = Callable[[object], object]


def extract_domain_values(
    contract: MappingContract,
    native: Mapping[str, Any],
    domain: ObservationDomain,
    *,
    transforms: Mapping[str, Transform] | None = None,
) -> dict[str, object]:
    """Apply safe declarative rules for one source-native observation domain.

    The writer executes only selectors supported by ``extract_declared_value``. XML/XPath
    cardinality and namespace handling remains source-parser-owned. Transforms are supplied
    explicitly by the jurisdiction adapter; the framework has no global semantic transform
    registry and must not invent legal/business meaning.
    """
    errors = contract.validate()
    if errors:
        raise ValueError("; ".join(errors))

    rules = tuple(rule for rule in contract.rules if rule.domain == domain)
    if not rules:
        raise ValueError(f"mapping contract has no rules for domain {domain.value}")

    available_transforms = transforms or {}
    values: dict[str, object] = {}
    for rule in rules:
        try:
            value = extract_declared_value(native, rule)
        except KeyError as exc:
            if rule.required:
                raise ValueError(
                    f"required source selector missing: {rule.source_selector}"
                ) from exc
            continue

        if rule.transform_id is not None:
            transform = available_transforms.get(rule.transform_id)
            if transform is None:
                raise ValueError(
                    f"mapping transform not supplied: {rule.transform_id}"
                )
            value = transform(value)

        if rule.repeated:
            if not isinstance(value, (list, tuple)):
                raise ValueError(
                    f"repeated mapping requires list/tuple value: {rule.source_selector}"
                )
            value = list(value)

        values[rule.target_field] = value
    return values


def build_observation_row(
    *,
    contract: MappingContract,
    domain: ObservationDomain,
    native: Mapping[str, Any],
    record_key: str,
    source_object_id: uuid.UUID,
    source_index: int,
    parser_version: str,
    source_payload: Mapping[str, object] | None = None,
    transforms: Mapping[str, Transform] | None = None,
) -> ObservationRow:
    """Build one immutable source-native observation from a reviewed mapping contract.

    ``record_key`` is deliberately supplied by the jurisdiction adapter. The factory does
    not invent or concatenate jurisdiction identity fields into a generic key.
    """
    if not record_key.strip():
        raise ValueError("record_key must not be blank")
    if source_index < 1:
        raise ValueError("source_index must be >= 1")
    if not parser_version.strip():
        raise ValueError("parser_version must not be blank")

    native_values = extract_domain_values(
        contract,
        native,
        domain,
        transforms=transforms,
    )
    payload = dict(native) if source_payload is None else dict(source_payload)
    return ObservationRow(
        record_key=record_key,
        source_object_id=source_object_id,
        source_index=source_index,
        native_values=native_values,
        source_payload=payload,
        parser_version=parser_version,
        mapping_version=contract.version,
    )


def append_mapped_observation(
    cur,
    spec: ObservationTableSpec,
    *,
    contract: MappingContract,
    native: Mapping[str, Any],
    record_key: str,
    source_object_id: uuid.UUID,
    source_index: int,
    parser_version: str,
    source_payload: Mapping[str, object] | None = None,
    transforms: Mapping[str, Transform] | None = None,
) -> bool:
    """Map and append one observation through the reusable native-store boundary."""
    domain = spec.domain
    row = build_observation_row(
        contract=contract,
        domain=domain,
        native=native,
        record_key=record_key,
        source_object_id=source_object_id,
        source_index=source_index,
        parser_version=parser_version,
        source_payload=source_payload,
        transforms=transforms,
    )
    declared_columns = {column.name for column in spec.native_columns}
    mapped_columns = set(row.native_values)
    unknown = sorted(mapped_columns - declared_columns)
    if unknown:
        raise ValueError(
            "mapping targets missing from native observation table: " + ", ".join(unknown)
        )
    return append_observation(cur, spec, row)
