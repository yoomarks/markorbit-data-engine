from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from app.global_trademarks.au_ipgod import (
    ingest_application,
    ingest_application_classification,
    ingest_application_description,
    ingest_application_events,
    ingest_application_links,
    ingest_party_activity,
)
from app.global_trademarks.ca_st96 import ingest_cipo_st96_core
from app.global_trademarks.gb_open_data import ingest_ukipo_2018
from app.global_trademarks.preflight import (
    SourcePreflight,
    inspect_au_ipgod,
    inspect_ca_st96,
    inspect_gb_2018,
    inspect_tm_link,
)
from app.global_trademarks.tm_link_seed import (
    ingest_tm_link_applicants,
    ingest_tm_link_applications,
    ingest_tm_link_classes,
    ingest_tm_link_details,
)
from app.trademark_framework.registry import country_pack
from app.trademark_framework.runtime import (
    RuntimeAdapterRegistry,
    RuntimeRequest,
    RuntimeSourceKey,
)


_TM_LINK_LOADERS: Mapping[str, Callable[..., int]] = {
    "applications": ingest_tm_link_applications,
    "applicants": ingest_tm_link_applicants,
    "details": ingest_tm_link_details,
    "classes": ingest_tm_link_classes,
}

_AU_LOADERS: Mapping[str, Callable[..., int]] = {
    "application": ingest_application,
    "party-activity": ingest_party_activity,
    "application-links": ingest_application_links,
    "application-events": ingest_application_events,
    "application-classification": ingest_application_classification,
    "application-description": ingest_application_description,
}

_GB_STREAMS = ("DOMESTIC", "MADRID_IR")
_CA_SOURCES = ("CIPO_GLOBAL_2025_06_14", "CIPO_WEEKLY")


def _option(options: Mapping[str, Any], key: str) -> Any:
    if key not in options:
        raise ValueError(f"runtime option is required: {key}")
    return options[key]


def _source_parser_version(jurisdiction: str, source_id: str) -> str:
    source = country_pack(jurisdiction).source(source_id)
    parser_version = (source.parser_version or "").strip()
    if not parser_version:
        raise RuntimeError(
            f"source has no parser_version in jurisdiction framework: {jurisdiction}:{source_id}"
        )
    return parser_version


def _validated_choice(
    metadata: Mapping[str, object],
    key: str,
    choices: tuple[str, ...],
) -> str:
    value = str(metadata.get(key) or "").strip()
    if value not in choices:
        raise ValueError(f"{key} must be one of {choices}, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class FunctionalRuntimeAdapter:
    adapter_id: str
    commands: tuple[str, ...]
    source_keys: tuple[RuntimeSourceKey, ...]
    selector_choices: Mapping[str, tuple[str, ...]]
    _request_from_command: Callable[[str, Mapping[str, Any]], RuntimeRequest]
    _request_from_source: Callable[..., RuntimeRequest]
    _preflight: Callable[[RuntimeRequest, int], SourcePreflight]
    _execute: Callable[[RuntimeRequest], int]

    def request_from_command(
        self,
        command: str,
        options: Mapping[str, Any],
    ) -> RuntimeRequest:
        if command not in self.commands:
            raise ValueError(f"{self.adapter_id} does not support command: {command}")
        return self._request_from_command(command, options).normalized()

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
        )
        normalized = request.normalized()
        if RuntimeSourceKey(normalized.jurisdiction, normalized.source_id).as_tuple() not in {
            key.as_tuple() for key in self.source_keys
        }:
            raise ValueError(
                f"{self.adapter_id} does not support source: "
                f"{normalized.jurisdiction}:{normalized.source_id}"
            )
        return normalized

    def preflight(self, request: RuntimeRequest, *, sample_limit: int = 100) -> SourcePreflight:
        if sample_limit < 1:
            raise ValueError("sample_limit must be positive")
        return self._preflight(request.normalized(), sample_limit)

    def execute(self, request: RuntimeRequest) -> int:
        return self._execute(request.normalized())


def _gb_request_from_command(command: str, options: Mapping[str, Any]) -> RuntimeRequest:
    stream = str(_option(options, "stream")).strip()
    return _gb_request_from_source(
        jurisdiction="GB",
        source_id="UKIPO_OPEN_DATA_2018",
        path=_option(options, "path"),
        metadata={"source_stream": stream},
        max_records=options.get("max_records"),
        command=command,
    )


def _gb_request_from_source(
    *,
    jurisdiction: str,
    source_id: str,
    path: Path,
    metadata: Mapping[str, object],
    max_records: int | None,
    command: str | None = None,
) -> RuntimeRequest:
    if jurisdiction.strip().upper() not in {"GB", "UK"}:
        raise ValueError("UKIPO 2018 runtime requires GB jurisdiction")
    if source_id != "UKIPO_OPEN_DATA_2018":
        raise ValueError("UKIPO 2018 runtime source_id mismatch")
    stream = _validated_choice(metadata, "source_stream", _GB_STREAMS)
    return RuntimeRequest(
        jurisdiction="GB",
        source_id=source_id,
        path=Path(path),
        parser_version=_source_parser_version("GB", source_id),
        metadata={"source_stream": stream, "max_records": max_records},
        max_records=max_records,
        command=command,
    )


def _gb_preflight(request: RuntimeRequest, sample_limit: int) -> SourcePreflight:
    return inspect_gb_2018(request.path, sample_limit=sample_limit)


def _gb_execute(request: RuntimeRequest) -> int:
    stream = _validated_choice(request.metadata, "source_stream", _GB_STREAMS)
    return ingest_ukipo_2018(
        request.path,
        source_stream=stream,
        max_records=request.max_records,
    )


def _tm_link_request_from_command(command: str, options: Mapping[str, Any]) -> RuntimeRequest:
    jurisdiction = str(_option(options, "jurisdiction")).strip().upper()
    if jurisdiction == "EM":
        jurisdiction = "EU"
    source_id = f"TM_LINK_{jurisdiction}"
    return _tm_link_request_from_source(
        jurisdiction=jurisdiction,
        source_id=source_id,
        path=_option(options, "path"),
        metadata={"source_table": str(_option(options, "table")).strip()},
        max_records=options.get("max_records"),
        command=command,
    )


def _tm_link_request_from_source(
    *,
    jurisdiction: str,
    source_id: str,
    path: Path,
    metadata: Mapping[str, object],
    max_records: int | None,
    command: str | None = None,
) -> RuntimeRequest:
    key = jurisdiction.strip().upper()
    if key == "EM":
        key = "EU"
    if key not in {"EU", "NZ"}:
        raise ValueError("TM-Link runtime supports only EU and NZ")
    if source_id != f"TM_LINK_{key}":
        raise ValueError("TM-Link runtime source_id mismatch")
    table = _validated_choice(metadata, "source_table", tuple(_TM_LINK_LOADERS))
    return RuntimeRequest(
        jurisdiction=key,
        source_id=source_id,
        path=Path(path),
        parser_version=_source_parser_version(key, source_id),
        metadata={"source_table": table, "max_records": max_records},
        max_records=max_records,
        command=command,
    )


def _tm_link_preflight(request: RuntimeRequest, sample_limit: int) -> SourcePreflight:
    table = _validated_choice(request.metadata, "source_table", tuple(_TM_LINK_LOADERS))
    return inspect_tm_link(
        request.path,
        jurisdiction=request.jurisdiction,
        table=table,
        sample_limit=sample_limit,
    )


def _tm_link_execute(request: RuntimeRequest) -> int:
    table = _validated_choice(request.metadata, "source_table", tuple(_TM_LINK_LOADERS))
    return _TM_LINK_LOADERS[table](
        request.path,
        jurisdiction=request.jurisdiction,
        max_records=request.max_records,
    )


def _au_request_from_command(command: str, options: Mapping[str, Any]) -> RuntimeRequest:
    return _au_request_from_source(
        jurisdiction="AU",
        source_id="IPGOD_2022",
        path=_option(options, "path"),
        metadata={"source_table": str(_option(options, "table")).strip()},
        max_records=options.get("max_records"),
        command=command,
    )


def _au_request_from_source(
    *,
    jurisdiction: str,
    source_id: str,
    path: Path,
    metadata: Mapping[str, object],
    max_records: int | None,
    command: str | None = None,
) -> RuntimeRequest:
    if jurisdiction.strip().upper() != "AU" or source_id != "IPGOD_2022":
        raise ValueError("IPGOD runtime requires AU:IPGOD_2022")
    table = _validated_choice(metadata, "source_table", tuple(_AU_LOADERS))
    return RuntimeRequest(
        jurisdiction="AU",
        source_id=source_id,
        path=Path(path),
        parser_version=_source_parser_version("AU", source_id),
        metadata={"source_table": table, "max_records": max_records},
        max_records=max_records,
        command=command,
    )


def _au_preflight(request: RuntimeRequest, sample_limit: int) -> SourcePreflight:
    table = _validated_choice(request.metadata, "source_table", tuple(_AU_LOADERS))
    return inspect_au_ipgod(request.path, table=table, sample_limit=sample_limit)


def _au_execute(request: RuntimeRequest) -> int:
    table = _validated_choice(request.metadata, "source_table", tuple(_AU_LOADERS))
    return _AU_LOADERS[table](request.path, max_records=request.max_records)


def _ca_request_from_command(command: str, options: Mapping[str, Any]) -> RuntimeRequest:
    source_id = str(options.get("source_id") or "CIPO_GLOBAL_2025_06_14").strip()
    return _ca_request_from_source(
        jurisdiction="CA",
        source_id=source_id,
        path=_option(options, "path"),
        metadata={},
        max_records=options.get("max_records"),
        command=command,
    )


def _ca_request_from_source(
    *,
    jurisdiction: str,
    source_id: str,
    path: Path,
    metadata: Mapping[str, object],
    max_records: int | None,
    command: str | None = None,
) -> RuntimeRequest:
    if jurisdiction.strip().upper() != "CA":
        raise ValueError("CIPO ST.96 runtime requires CA jurisdiction")
    if source_id not in _CA_SOURCES:
        raise ValueError(f"unsupported CIPO ST.96 source_id: {source_id}")
    unexpected = sorted(key for key in metadata if key not in {"max_records"})
    if unexpected:
        raise ValueError(f"CIPO ST.96 runtime does not accept selectors: {unexpected}")
    return RuntimeRequest(
        jurisdiction="CA",
        source_id=source_id,
        path=Path(path),
        parser_version=_source_parser_version("CA", source_id),
        metadata={"source_kind": "CIPO_ST96_CORE", "max_records": max_records},
        max_records=max_records,
        command=command,
    )


def _ca_preflight(request: RuntimeRequest, sample_limit: int) -> SourcePreflight:
    return inspect_ca_st96(request.path, sample_limit=sample_limit)


def _ca_execute(request: RuntimeRequest) -> int:
    return ingest_cipo_st96_core(
        request.path,
        source_id=request.source_id,
        max_records=request.max_records,
    )


GB_2018_RUNTIME = FunctionalRuntimeAdapter(
    adapter_id="UKIPO_2018_RUNTIME_V1",
    commands=("ingest-gb-2018",),
    source_keys=(RuntimeSourceKey("GB", "UKIPO_OPEN_DATA_2018"),),
    selector_choices={"source_stream": _GB_STREAMS},
    _request_from_command=_gb_request_from_command,
    _request_from_source=_gb_request_from_source,
    _preflight=_gb_preflight,
    _execute=_gb_execute,
)

TM_LINK_RUNTIME = FunctionalRuntimeAdapter(
    adapter_id="TM_LINK_RUNTIME_V1",
    commands=("ingest-tm-link",),
    source_keys=(
        RuntimeSourceKey("EU", "TM_LINK_EU"),
        RuntimeSourceKey("NZ", "TM_LINK_NZ"),
    ),
    selector_choices={"source_table": tuple(_TM_LINK_LOADERS)},
    _request_from_command=_tm_link_request_from_command,
    _request_from_source=_tm_link_request_from_source,
    _preflight=_tm_link_preflight,
    _execute=_tm_link_execute,
)

AU_IPGOD_RUNTIME = FunctionalRuntimeAdapter(
    adapter_id="IPGOD_2022_RUNTIME_V1",
    commands=("ingest-au-ipgod",),
    source_keys=(RuntimeSourceKey("AU", "IPGOD_2022"),),
    selector_choices={"source_table": tuple(_AU_LOADERS)},
    _request_from_command=_au_request_from_command,
    _request_from_source=_au_request_from_source,
    _preflight=_au_preflight,
    _execute=_au_execute,
)

CA_ST96_RUNTIME = FunctionalRuntimeAdapter(
    adapter_id="CIPO_ST96_RUNTIME_V1",
    commands=("ingest-ca-st96",),
    source_keys=tuple(RuntimeSourceKey("CA", source_id) for source_id in _CA_SOURCES),
    selector_choices={},
    _request_from_command=_ca_request_from_command,
    _request_from_source=_ca_request_from_source,
    _preflight=_ca_preflight,
    _execute=_ca_execute,
)

RUNTIME_REGISTRY = RuntimeAdapterRegistry(
    (GB_2018_RUNTIME, TM_LINK_RUNTIME, AU_IPGOD_RUNTIME, CA_ST96_RUNTIME)
)


def runtime_registry() -> RuntimeAdapterRegistry:
    audit = RUNTIME_REGISTRY.audit()
    if not audit.ready:
        raise RuntimeError(f"trademark runtime registry is invalid: {audit.errors}")
    return RUNTIME_REGISTRY
