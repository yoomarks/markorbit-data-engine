from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol


RUNTIME_ADAPTER_VERSION = "TRADEMARK_RUNTIME_ADAPTER_V2"


class PreflightResult(Protocol):
    schema_valid: bool

    def as_dict(self) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class RuntimeSourceKey:
    jurisdiction: str
    source_id: str

    def normalized(self) -> "RuntimeSourceKey":
        return RuntimeSourceKey(
            jurisdiction=self.jurisdiction.strip().upper(),
            source_id=self.source_id.strip(),
        )

    def as_tuple(self) -> tuple[str, str]:
        normalized = self.normalized()
        return normalized.jurisdiction, normalized.source_id


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    jurisdiction: str
    source_id: str
    path: Path
    parser_version: str
    metadata: Mapping[str, object] = field(default_factory=dict)
    max_records: int | None = None
    command: str | None = None

    def normalized(self) -> "RuntimeRequest":
        jurisdiction = self.jurisdiction.strip().upper()
        source_id = self.source_id.strip()
        parser_version = self.parser_version.strip()
        if not jurisdiction or not source_id:
            raise ValueError("runtime request jurisdiction/source_id are required")
        if not parser_version:
            raise ValueError("runtime request parser_version is required")
        if self.max_records is not None and self.max_records < 1:
            raise ValueError("runtime request max_records must be positive")
        return RuntimeRequest(
            jurisdiction=jurisdiction,
            source_id=source_id,
            path=self.path,
            parser_version=parser_version,
            metadata=dict(self.metadata),
            max_records=self.max_records,
            command=self.command,
        )


class SourceRuntimeAdapter(Protocol):
    adapter_id: str
    commands: tuple[str, ...]
    source_keys: tuple[RuntimeSourceKey, ...]
    selector_choices: Mapping[str, tuple[str, ...]]

    def request_from_command(
        self,
        command: str,
        options: Mapping[str, Any],
    ) -> RuntimeRequest: ...

    def request_from_source(
        self,
        *,
        jurisdiction: str,
        source_id: str,
        path: Path,
        metadata: Mapping[str, object],
        max_records: int | None,
    ) -> RuntimeRequest: ...

    def preflight(self, request: RuntimeRequest, *, sample_limit: int = 100) -> PreflightResult: ...

    def execute(self, request: RuntimeRequest) -> int: ...


RequestFromCommand = Callable[[str, Mapping[str, Any]], RuntimeRequest]
RequestFromSource = Callable[..., RuntimeRequest]
PreflightCallable = Callable[[RuntimeRequest, int], PreflightResult]
ExecuteCallable = Callable[[RuntimeRequest], int]


@dataclass(frozen=True, slots=True)
class FunctionalRuntimeAdapter:
    """Reusable runtime adapter for plugin-style jurisdiction modules.

    A new country needs source dispatch for the generic ``ingest-source`` path but does not need
    a bespoke CLI command. Therefore ``commands`` may be empty while ``source_keys`` remains
    mandatory. Legacy command adapters can still provide ``request_from_command`` explicitly.
    """

    adapter_id: str
    source_keys: tuple[RuntimeSourceKey, ...]
    _request_from_source: RequestFromSource
    _preflight: PreflightCallable
    _execute: ExecuteCallable
    commands: tuple[str, ...] = ()
    selector_choices: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    _request_from_command: RequestFromCommand | None = None

    def request_from_command(
        self,
        command: str,
        options: Mapping[str, Any],
    ) -> RuntimeRequest:
        normalized_command = command.strip()
        if normalized_command not in self.commands or self._request_from_command is None:
            raise ValueError(f"{self.adapter_id} does not support command: {command}")
        return self._request_from_command(normalized_command, options).normalized()

    def request_from_source(
        self,
        *,
        jurisdiction: str,
        source_id: str,
        path: Path,
        metadata: Mapping[str, object],
        max_records: int | None,
    ) -> RuntimeRequest:
        request = self._request_from_source(
            jurisdiction=jurisdiction,
            source_id=source_id,
            path=path,
            metadata=metadata,
            max_records=max_records,
        ).normalized()
        supported = {key.as_tuple() for key in self.source_keys}
        if RuntimeSourceKey(request.jurisdiction, request.source_id).as_tuple() not in supported:
            raise ValueError(
                f"{self.adapter_id} does not support source: "
                f"{request.jurisdiction}:{request.source_id}"
            )
        return request

    def preflight(self, request: RuntimeRequest, *, sample_limit: int = 100) -> PreflightResult:
        if sample_limit < 1:
            raise ValueError("sample_limit must be positive")
        return self._preflight(request.normalized(), sample_limit)

    def execute(self, request: RuntimeRequest) -> int:
        return self._execute(request.normalized())


@dataclass(frozen=True, slots=True)
class RuntimeRegistryAudit:
    version: str
    adapter_count: int
    command_count: int
    source_key_count: int
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "adapter_count": self.adapter_count,
            "command_count": self.command_count,
            "source_key_count": self.source_key_count,
            "ready": self.ready,
            "errors": list(self.errors),
        }


class RuntimeAdapterRegistry:
    def __init__(self, adapters: tuple[SourceRuntimeAdapter, ...]) -> None:
        self._adapters = adapters
        self._by_command: dict[str, SourceRuntimeAdapter] = {}
        self._by_source: dict[tuple[str, str], SourceRuntimeAdapter] = {}
        errors: list[str] = []

        adapter_ids: set[str] = set()
        for adapter in adapters:
            adapter_id = adapter.adapter_id.strip()
            if not adapter_id:
                errors.append("runtime adapter_id must not be blank")
            elif adapter_id in adapter_ids:
                errors.append(f"duplicate runtime adapter_id: {adapter_id}")
            adapter_ids.add(adapter_id)

            # Source-only adapters are first-class for generic ingest-source onboarding.
            # A country should not need to add a bespoke top-level CLI command merely to register.
            if not adapter.source_keys:
                errors.append(f"{adapter_id}: runtime adapter must expose at least one source key")

            for command in adapter.commands:
                normalized_command = command.strip()
                if not normalized_command:
                    errors.append(f"{adapter_id}: blank command")
                    continue
                if normalized_command in self._by_command:
                    errors.append(f"duplicate runtime command: {normalized_command}")
                    continue
                self._by_command[normalized_command] = adapter

            for source_key in adapter.source_keys:
                key = source_key.as_tuple()
                if not key[0] or not key[1]:
                    errors.append(f"{adapter_id}: blank runtime source key")
                    continue
                if key in self._by_source:
                    errors.append(f"duplicate runtime source key: {key[0]}:{key[1]}")
                    continue
                self._by_source[key] = adapter

        self._audit = RuntimeRegistryAudit(
            version=RUNTIME_ADAPTER_VERSION,
            adapter_count=len(adapters),
            command_count=len(self._by_command),
            source_key_count=len(self._by_source),
            errors=tuple(errors),
        )

    def audit(self) -> RuntimeRegistryAudit:
        return self._audit

    def adapters(self) -> tuple[SourceRuntimeAdapter, ...]:
        return self._adapters

    def for_command(self, command: str) -> SourceRuntimeAdapter:
        try:
            return self._by_command[command.strip()]
        except KeyError as exc:
            raise ValueError(f"unsupported trademark runtime command: {command}") from exc

    def for_source(self, jurisdiction: str, source_id: str) -> SourceRuntimeAdapter:
        requested = jurisdiction.strip().upper()
        canonical = requested
        try:
            from app.trademark_framework.registry import country_pack

            canonical = country_pack(requested).jurisdiction
        except ValueError:
            pass
        key = (canonical, source_id.strip())
        try:
            return self._by_source[key]
        except KeyError as exc:
            raise ValueError(f"no runtime adapter for trademark source: {key[0]}:{key[1]}") from exc
