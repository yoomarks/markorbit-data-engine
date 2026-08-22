from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from app.trademark_framework.contracts import CountryPack, ObservationDomain


MAPPING_CONTRACT_VERSION = "TRADEMARK_SOURCE_MAPPING_CONTRACT_V1"


class SelectorKind(StrEnum):
    FIELD = "FIELD"
    COLUMN = "COLUMN"
    JSON_POINTER = "JSON_POINTER"
    XPATH = "XPATH"
    XML_LOCAL_PATH = "XML_LOCAL_PATH"


@dataclass(frozen=True, slots=True)
class MappingRule:
    """One source-native selector mapped into one country-native fact field.

    This contract deliberately does not define legal/business semantics. A selector may
    populate only a field in an observation domain already declared by the CountryPack.
    Source-specific parsers remain free to handle structures that cannot be expressed
    safely through a declarative selector.
    """

    selector_kind: SelectorKind
    source_selector: str
    domain: ObservationDomain
    target_field: str
    required: bool = False
    repeated: bool = False
    transform_id: str | None = None

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not self.source_selector.strip():
            errors.append("mapping source_selector must not be blank")
        if not self.target_field.strip():
            errors.append("mapping target_field must not be blank")
        if self.transform_id is not None and not self.transform_id.strip():
            errors.append("mapping transform_id must not be blank")
        if self.selector_kind == SelectorKind.JSON_POINTER and not self.source_selector.startswith("/"):
            errors.append("JSON_POINTER selector must begin with '/'")
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class MappingContract:
    jurisdiction: str
    source_id: str
    version: str
    rules: tuple[MappingRule, ...]
    identity_targets: tuple[str, ...] = ()
    notes: str = ""

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not self.jurisdiction.strip():
            errors.append("mapping jurisdiction is required")
        if not self.source_id.strip():
            errors.append("mapping source_id is required")
        if not self.version.strip():
            errors.append("mapping version is required")
        if not self.rules:
            errors.append("mapping requires at least one rule")
        seen: set[tuple[SelectorKind, str, ObservationDomain, str]] = set()
        targets: set[str] = set()
        for rule in self.rules:
            errors.extend(rule.validate())
            key = (rule.selector_kind, rule.source_selector, rule.domain, rule.target_field)
            if key in seen:
                errors.append(
                    "duplicate mapping rule: "
                    f"{rule.selector_kind.value}:{rule.source_selector} -> "
                    f"{rule.domain.value}.{rule.target_field}"
                )
            seen.add(key)
            targets.add(rule.target_field)
        missing_identity_targets = sorted(set(self.identity_targets) - targets)
        if missing_identity_targets:
            errors.append(
                "identity_targets missing mapping rules: " + ", ".join(missing_identity_targets)
            )
        return tuple(errors)

    def validate_against(self, pack: CountryPack) -> tuple[str, ...]:
        errors = list(self.validate())
        if self.jurisdiction.strip().upper() != pack.jurisdiction:
            errors.append(
                f"mapping jurisdiction mismatch: {self.jurisdiction!r} != {pack.jurisdiction!r}"
            )
        try:
            pack.source(self.source_id)
        except ValueError:
            errors.append(f"mapping source not declared in CountryPack: {self.source_id}")
        allowed_domains = set(pack.observation_domains)
        for rule in self.rules:
            if rule.domain not in allowed_domains:
                errors.append(
                    f"mapping domain {rule.domain.value} not declared by {pack.jurisdiction} CountryPack"
                )
        return tuple(errors)

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": MAPPING_CONTRACT_VERSION,
            "jurisdiction": self.jurisdiction,
            "source_id": self.source_id,
            "version": self.version,
            "identity_targets": list(self.identity_targets),
            "rules": [
                {
                    "selector_kind": rule.selector_kind.value,
                    "source_selector": rule.source_selector,
                    "domain": rule.domain.value,
                    "target_field": rule.target_field,
                    "required": rule.required,
                    "repeated": rule.repeated,
                    "transform_id": rule.transform_id,
                }
                for rule in self.rules
            ],
            "notes": self.notes,
        }


def _json_pointer(value: object, pointer: str) -> object:
    current: object = value
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise KeyError(pointer)
            current = current[token]
            continue
        if isinstance(current, (list, tuple)):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise KeyError(pointer) from exc
            continue
        raise KeyError(pointer)
    return current


def extract_declared_value(native: Mapping[str, Any], rule: MappingRule) -> object:
    """Extract safe declarative selectors for JSON/tabular sources.

    XML/XPath selectors intentionally remain parser-owned in V1 because namespace and
    repeated-node semantics must be verified against the authority schema rather than
    guessed generically.
    """
    if rule.selector_kind in {SelectorKind.FIELD, SelectorKind.COLUMN}:
        if rule.source_selector not in native:
            raise KeyError(rule.source_selector)
        return native[rule.source_selector]
    if rule.selector_kind == SelectorKind.JSON_POINTER:
        return _json_pointer(native, rule.source_selector)
    raise NotImplementedError(
        f"{rule.selector_kind.value} extraction remains source-parser-owned in mapping V1"
    )
