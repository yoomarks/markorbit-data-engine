from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.trademark_factory.mapping import MappingContract
from app.trademark_factory.writer import Transform, append_mapped_observation
from app.trademark_framework.contracts import CountryPack, ObservationDomain
from app.trademark_framework.native_store import ObservationTableSpec, install_observation_table


NATIVE_STORE_BUNDLE_VERSION = "TRADEMARK_NATIVE_STORE_BUNDLE_V1"
_BINDING_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class StoreBinding:
    """Bind one reviewed mapping contract to one jurisdiction-native observation table."""

    binding_id: str
    spec: ObservationTableSpec
    contract: MappingContract

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not _BINDING_ID_RE.fullmatch(self.binding_id):
            errors.append(f"invalid store binding id: {self.binding_id!r}")
        errors.extend(self.spec.validate())
        errors.extend(self.contract.validate())

        domain_rules = tuple(rule for rule in self.contract.rules if rule.domain == self.spec.domain)
        if not domain_rules:
            errors.append(
                f"{self.binding_id}: mapping contract has no {self.spec.domain.value} rules"
            )
            return tuple(errors)

        declared_columns = {column.name for column in self.spec.native_columns}
        mapped_columns = {rule.target_field for rule in domain_rules}
        unknown = sorted(mapped_columns - declared_columns)
        if unknown:
            errors.append(
                f"{self.binding_id}: mapping targets missing from table: " + ", ".join(unknown)
            )

        required_columns = {
            column.name for column in self.spec.native_columns if not column.nullable
        }
        unmapped_required = sorted(required_columns - mapped_columns)
        if unmapped_required:
            errors.append(
                f"{self.binding_id}: required table columns lack mapping rules: "
                + ", ".join(unmapped_required)
            )
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class NativeStoreBundle:
    """Reusable source-specific bundle of native observation tables and mappings.

    The bundle standardizes schema/mapping orchestration only. It does not define a global
    trademark schema, source identity, current-state ordering, legal status, or asset semantics.
    Multiple bindings may use the same observation domain when a jurisdiction needs separate
    source-native table families.
    """

    jurisdiction: str
    source_id: str
    store_schema: str
    bindings: tuple[StoreBinding, ...]

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        jurisdiction = self.jurisdiction.strip().upper()
        source_id = self.source_id.strip()
        store_schema = self.store_schema.strip()
        if not jurisdiction:
            errors.append("native store bundle jurisdiction is required")
        if not source_id:
            errors.append("native store bundle source_id is required")
        if not store_schema:
            errors.append("native store bundle store_schema is required")
        if not self.bindings:
            errors.append("native store bundle requires at least one binding")

        binding_ids = [binding.binding_id for binding in self.bindings]
        if len(set(binding_ids)) != len(binding_ids):
            errors.append("native store bundle binding ids must be unique")
        qualified_tables = [binding.spec.qualified_name for binding in self.bindings]
        if len(set(qualified_tables)) != len(qualified_tables):
            errors.append("native store bundle tables must be unique")

        for binding in self.bindings:
            errors.extend(binding.validate())
            if binding.spec.schema_name != store_schema:
                errors.append(
                    f"{binding.binding_id}: table schema {binding.spec.schema_name!r} "
                    f"does not match bundle store_schema {store_schema!r}"
                )
            if binding.contract.jurisdiction.strip().upper() != jurisdiction:
                errors.append(
                    f"{binding.binding_id}: mapping jurisdiction "
                    f"{binding.contract.jurisdiction!r} does not match {jurisdiction!r}"
                )
            if binding.contract.source_id.strip() != source_id:
                errors.append(
                    f"{binding.binding_id}: mapping source {binding.contract.source_id!r} "
                    f"does not match {source_id!r}"
                )
        return tuple(errors)

    def validate_against(self, pack: CountryPack) -> tuple[str, ...]:
        errors = list(self.validate())
        if self.jurisdiction.strip().upper() != pack.jurisdiction:
            errors.append(
                f"bundle jurisdiction mismatch: {self.jurisdiction!r} != {pack.jurisdiction!r}"
            )
        if self.store_schema.strip() != pack.store_schema:
            errors.append(
                f"bundle store_schema mismatch: {self.store_schema!r} != {pack.store_schema!r}"
            )
        try:
            pack.source(self.source_id)
        except ValueError:
            errors.append(f"bundle source not declared in CountryPack: {self.source_id}")

        allowed_domains = set(pack.observation_domains)
        for binding in self.bindings:
            if binding.spec.domain not in allowed_domains:
                errors.append(
                    f"{binding.binding_id}: domain {binding.spec.domain.value} not declared by "
                    f"{pack.jurisdiction} CountryPack"
                )
            errors.extend(binding.contract.validate_against(pack))
        return tuple(errors)

    def binding(self, binding_id: str) -> StoreBinding:
        wanted = binding_id.strip()
        for binding in self.bindings:
            if binding.binding_id == wanted:
                return binding
        raise ValueError(f"unsupported native-store binding: {binding_id}")

    def bindings_for_domain(self, domain: ObservationDomain) -> tuple[StoreBinding, ...]:
        return tuple(binding for binding in self.bindings if binding.spec.domain == domain)


@dataclass(frozen=True, slots=True)
class BundleAppendResult:
    inserted_by_binding: Mapping[str, bool]

    @property
    def inserted_count(self) -> int:
        return sum(1 for inserted in self.inserted_by_binding.values() if inserted)

    @property
    def replay_count(self) -> int:
        return sum(1 for inserted in self.inserted_by_binding.values() if not inserted)

    def as_dict(self) -> dict[str, object]:
        return {
            "inserted_by_binding": dict(self.inserted_by_binding),
            "inserted_count": self.inserted_count,
            "replay_count": self.replay_count,
        }


def install_native_store_bundle(cur, bundle: NativeStoreBundle) -> None:
    """Install additive native observation tables in the caller's explicit migration tx."""
    errors = bundle.validate()
    if errors:
        raise ValueError("; ".join(errors))
    for binding in bundle.bindings:
        install_observation_table(cur, binding.spec)


def append_native_record_bundle(
    cur,
    bundle: NativeStoreBundle,
    *,
    native: Mapping[str, Any],
    record_key: str,
    source_object_id: uuid.UUID,
    source_index: int,
    parser_version: str,
    source_payload: Mapping[str, object] | None = None,
    transforms: Mapping[str, Transform] | None = None,
) -> BundleAppendResult:
    """Map one source-native record into every declared bundle binding.

    All writes use the caller's cursor/transaction. If any binding fails validation, required
    extraction, transform lookup, or deterministic replay checks, the caller can roll back the
    whole record bundle rather than keeping a partially mapped observation family.
    """
    errors = bundle.validate()
    if errors:
        raise ValueError("; ".join(errors))

    inserted: dict[str, bool] = {}
    for binding in bundle.bindings:
        inserted[binding.binding_id] = append_mapped_observation(
            cur,
            binding.spec,
            contract=binding.contract,
            native=native,
            record_key=record_key,
            source_object_id=source_object_id,
            source_index=source_index,
            parser_version=parser_version,
            source_payload=source_payload,
            transforms=transforms,
        )
    return BundleAppendResult(inserted_by_binding=inserted)
