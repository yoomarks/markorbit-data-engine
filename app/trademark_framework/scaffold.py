from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.trademark_framework.contracts import (
    DataFormat,
    SourceAdapterKind,
    TransportKind,
    UpdateSemantics,
)


SCAFFOLD_VERSION = "TRADEMARK_COUNTRY_SCAFFOLD_V5"
_JURISDICTION_RE = re.compile(r"^[A-Z0-9]{2,4}$")


@dataclass(frozen=True, slots=True)
class ScaffoldRequest:
    jurisdiction: str
    source_id: str
    adapter_kind: SourceAdapterKind
    data_format: DataFormat
    update_semantics: UpdateSemantics
    transport: TransportKind
    store_schema: str | None = None

    def normalized(self) -> "ScaffoldRequest":
        jurisdiction = self.jurisdiction.strip().upper()
        source_id = self.source_id.strip().upper()
        store_schema = (self.store_schema or f"trademark_{jurisdiction.lower()}").strip()
        if not _JURISDICTION_RE.fullmatch(jurisdiction):
            raise ValueError("jurisdiction must be 2-4 uppercase letters/digits")
        if not source_id:
            raise ValueError("source_id is required")
        if not store_schema:
            raise ValueError("store_schema is required")
        return ScaffoldRequest(
            jurisdiction=jurisdiction,
            source_id=source_id,
            adapter_kind=self.adapter_kind,
            data_format=self.data_format,
            update_semantics=self.update_semantics,
            transport=self.transport,
            store_schema=store_schema,
        )


@dataclass(frozen=True, slots=True)
class ScaffoldPlan:
    version: str
    request: ScaffoldRequest
    files: dict[str, str]

    def as_dict(self, *, include_content: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "scaffold_version": self.version,
            "jurisdiction": self.request.jurisdiction,
            "source_id": self.request.source_id,
            "store_schema": self.request.store_schema,
            "adapter_kind": self.request.adapter_kind.value,
            "data_format": self.request.data_format.value,
            "update_semantics": self.request.update_semantics.value,
            "transport": self.request.transport.value,
            "files": sorted(self.files),
        }
        if include_content:
            payload["content"] = dict(self.files)
        return payload

    def write(self, root: Path) -> tuple[Path, ...]:
        root = root.resolve()
        written: list[Path] = []
        for relative_path, content in self.files.items():
            destination = (root / relative_path).resolve()
            if root not in destination.parents:
                raise RuntimeError("scaffold path escaped output root")
            if destination.exists():
                raise FileExistsError(f"refusing to overwrite scaffold file: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            written.append(destination)
        return tuple(written)


def _adapter_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\nfrom collections.abc import Iterator\nfrom pathlib import Path\n\n\nADAPTER_VERSION = "{request.jurisdiction}_TRADEMARK_ADAPTER_V1"\nSOURCE_ID = "{request.source_id}"\n\n\ndef iter_native_records(path: Path) -> Iterator[dict[str, object]]:\n    """Parse source-native records without inventing semantic/legal facts.\n\n    Replace this stub only after reviewing the authority's schema/data dictionary and\n    real sample payloads. Preserve unknown source fields in source_payload when useful.\n    The iteration order must be deterministic so source_index replay remains stable.\n    """\n    raise NotImplementedError("implement {request.jurisdiction} source-native parser")\n\n\ndef native_record_key(record: dict[str, object]) -> str:\n    """Return the source-declared record identity for deterministic replay."""\n    raise NotImplementedError("define {request.jurisdiction} source identity")\n\n\ndef source_operation(record: dict[str, object]) -> str:\n    """Return source operation semantics only when the authority explicitly declares them."""\n    return "OBSERVE"\n'''


def _country_template(request: ScaffoldRequest) -> str:
    return f'''from app.trademark_framework.contracts import (\n    AssetMode,\n    CountryPack,\n    CurrentProjectionContract,\n    CurrentProjectionMode,\n    DataFormat,\n    IdentityContract,\n    ObservationDomain,\n    SourceAdapterKind,\n    SourceDescriptor,\n    SourceRole,\n    TransportKind,\n    UpdateSemantics,\n)\n\n\nCOUNTRY_PACK = CountryPack(\n    jurisdiction="{request.jurisdiction}",\n    store_schema="{request.store_schema}",\n    identity=IdentityContract(\n        fields=("TODO_SOURCE_IDENTITY",),\n        notes="Replace only with source-declared identity after schema review.",\n    ),\n    observation_domains=(ObservationDomain.RECORD,),\n    current_projection=CurrentProjectionContract(\n        mode=CurrentProjectionMode.NOT_IMPLEMENTED,\n        notes="Do not promote current-state semantics until source ordering is proven.",\n    ),\n    asset_mode=AssetMode.NOT_IMPLEMENTED,\n    sources=(\n        SourceDescriptor(\n            source_id="{request.source_id}",\n            role=SourceRole.PRIMARY,\n            authoritative=True,\n            active_now=True,\n            pipeline_ready=False,\n            adapter_kind=SourceAdapterKind.{request.adapter_kind.name},\n            transport=TransportKind.{request.transport.name},\n            data_format=DataFormat.{request.data_format.name},\n            update_semantics=UpdateSemantics.{request.update_semantics.name},\n            notes="Generated scaffold; profile real source before enabling pipeline_ready.",\n        ),\n    ),\n)\n'''


def _mapping_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\nfrom app.trademark_factory.mapping import MappingContract\n\n\nMAPPING_VERSION = "{request.jurisdiction}_TRADEMARK_MAPPING_V1"\n\n\ndef build_mapping_contracts() -> tuple[MappingContract, ...]:\n    """Return reviewed source-selector -> country-native observation mappings.\n\n    Use MappingRule/SelectorKind only after profiling the authority schema. Keep mappings\n    source-faithful and do not invent global statuses, renewal opportunities, brand families,\n    legal conclusions or business semantics in Data Engine. XML namespace/cardinality semantics\n    may remain parser-owned when declarative extraction is not safe.\n    """\n    raise NotImplementedError("define {request.jurisdiction} reviewed mapping contracts")\n'''


def _store_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\nfrom app.trademark_factory.store_bundle import NativeStoreBundle\n\n\nSTORE_BUNDLE_VERSION = "{request.jurisdiction}_TRADEMARK_STORE_BUNDLE_V1"\nSTORE_SCHEMA = "{request.store_schema}"\nSOURCE_ID = "{request.source_id}"\n\n\ndef build_native_store_bundle() -> NativeStoreBundle:\n    """Bind reviewed mappings to source-native append-only observation tables.\n\n    Define ObservationTableSpec/NativeColumn and StoreBinding only from the authority's real\n    fields. The shared native-store primitives supply provenance, parser/mapping lineage,\n    deterministic replay checks and transaction-safe multi-domain writes.\n    """\n    raise NotImplementedError("define {request.jurisdiction} source-native store bundle")\n'''


def _schema_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\nfrom app.trademark_factory.store_bundle import install_native_store_bundle\nfrom app.trademark_jurisdictions.{request.jurisdiction.lower()}.store import build_native_store_bundle\n\n\nSCHEMA_VERSION = "{request.jurisdiction}_TRADEMARK_SCHEMA_V1"\nSTORE_SCHEMA = "{request.store_schema}"\n\n\ndef install_schema(cur) -> None:\n    """Install additive source-native observation tables in an explicit migration transaction."""\n    install_native_store_bundle(cur, build_native_store_bundle())\n'''


def _preflight_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\nfrom pathlib import Path\n\n\nPREFLIGHT_VERSION = "{request.jurisdiction}_TRADEMARK_PREFLIGHT_V1"\n\n\ndef validate_source(path: Path) -> dict[str, object]:\n    """No-write validation of authority/source shape before any ingestion mutation."""\n    raise NotImplementedError("validate {request.jurisdiction} source headers/schema/sample identity")\n'''


def _generic_acquisition_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\nfrom app.trademark_framework.acquisition import AcquisitionPage, AcquisitionPageRequest\n\n\nACQUISITION_ADAPTER_ID = "{request.jurisdiction}_TRADEMARK_ACQUISITION_V1"\nSOURCE_ID = "{request.source_id}"\n\n\ndef initial_cursor() -> str | None:\n    """Return the authority's initial page/cursor token, or None for a single-object source."""\n    return None\n\n\ndef fetch_page(request: AcquisitionPageRequest) -> AcquisitionPage:\n    """Acquire one raw authority response and return only source-native pagination facts.\n\n    Keep credentials in the transport/runtime environment. Never include API keys, authorization\n    headers or passwords in page keys/cursors/ledger metadata. The shared acquisition executor\n    materializes raw bytes atomically with SHA256 and resumable lineage.\n    """\n    raise NotImplementedError("connect {request.jurisdiction} official source/API acquisition")\n'''


def _http_acquisition_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\nfrom collections.abc import Mapping\n\nfrom app.trademark_framework.acquisition import AcquisitionPageRequest\nfrom app.trademark_framework.http_acquisition import (\n    HttpPageInterpretation,\n    HttpPaginatedAcquisitionAdapter,\n)\nfrom app.trademark_framework.http_transport import HttpResponse\nfrom app.trademark_framework.pagination import (\n    OffsetLimitPagination,\n    OpaqueCursorPagination,\n    PageNumberPagination,\n)\n\n\nACQUISITION_ADAPTER_ID = "{request.jurisdiction}_TRADEMARK_HTTP_ACQUISITION_V1"\nSOURCE_ID = "{request.source_id}"\n\n\ndef runtime_headers(request: AcquisitionPageRequest) -> Mapping[str, str]:\n    """Load runtime credentials from the approved secret mechanism only.\n\n    Do not persist API keys, bearer tokens, cookies or passwords in page keys, cursors, source\n    metadata or acquisition-ledger fields.\n    """\n    return {{}}\n\n\ndef runtime_query(request: AcquisitionPageRequest) -> Mapping[str, str]:\n    """Return verified non-pagination query parameters required by the official endpoint."""\n    return {{}}\n\n\ndef interpret_page(\n    request: AcquisitionPageRequest,\n    response: HttpResponse,\n) -> HttpPageInterpretation:\n    """Interpret stable page identity and official continuation semantics.\n\n    Parse the authority response only after reviewing its documented schema and representative\n    samples. Return HasMoreContinuation or SourceCursorContinuation from the shared framework; do\n    not infer termination from a generic rule.\n    """\n    raise NotImplementedError("interpret {request.jurisdiction} official API page semantics")\n\n\ndef build_acquisition_adapter() -> HttpPaginatedAcquisitionAdapter:\n    """Build the shared HTTP acquisition bridge after the official API contract is verified.\n\n    Choose exactly one pagination helper that matches the authority:\n    PageNumberPagination, OffsetLimitPagination, or OpaqueCursorPagination.\n    """\n    _pagination_examples = (PageNumberPagination, OffsetLimitPagination, OpaqueCursorPagination)\n    del _pagination_examples\n    raise NotImplementedError("select {request.jurisdiction} official endpoint and pagination contract")\n'''


def _acquisition_template(request: ScaffoldRequest) -> str:
    if request.transport == TransportKind.HTTP_API:
        return _http_acquisition_template(request)
    return _generic_acquisition_template(request)


def _loader_template(request: ScaffoldRequest) -> str:
    code = request.jurisdiction.lower()
    return f'''from __future__ import annotations\n\nfrom collections.abc import Iterator\nfrom pathlib import Path\nfrom typing import Mapping\n\nfrom app.global_trademarks.source_objects import register_source_object\nfrom app.trademark_factory.native_ingest import (\n    NativeIngestResult,\n    NativeRecordEnvelope,\n    execute_native_ingest,\n)\nfrom app.trademark_framework.registry import resolve_pipeline_id\nfrom app.trademark_jurisdictions.{code}.adapter import iter_native_records, native_record_key\nfrom app.trademark_jurisdictions.{code}.store import build_native_store_bundle\n\n\nLOADER_VERSION = "{request.jurisdiction}_TRADEMARK_LOADER_V1"\nSOURCE_ID = "{request.source_id}"\n\n\ndef iter_record_envelopes(path: Path) -> Iterator[NativeRecordEnvelope]:\n    """Wrap deterministic authority records in the shared durable-ingest envelope."""\n    for source_index, native in enumerate(iter_native_records(path), start=1):\n        yield NativeRecordEnvelope(\n            source_index=source_index,\n            record_key=native_record_key(native),\n            native=native,\n            source_payload=native,\n        )\n\n\ndef execute_materialized_source(\n    *,\n    path: Path,\n    parser_version: str,\n    metadata: Mapping[str, object],\n    max_records: int | None,\n    batch_size: int = 500,\n) -> NativeIngestResult:\n    """Register the exact materialized object and reuse durable native-ingest mechanics."""\n    source_object_id = register_source_object(\n        jurisdiction="{request.jurisdiction}",\n        source_id=SOURCE_ID,\n        path=path,\n        metadata=dict(metadata),\n    )\n    pipeline_id = resolve_pipeline_id("{request.jurisdiction}", SOURCE_ID, metadata)\n    if pipeline_id is None:\n        raise RuntimeError(\n            "source pipeline route is not configured; review CountryPack before enabling apply"\n        )\n    return execute_native_ingest(\n        source_object_id=source_object_id,\n        pipeline_id=pipeline_id,\n        bundle=build_native_store_bundle(),\n        parser_version=parser_version,\n        records=iter_record_envelopes(path),\n        batch_size=batch_size,\n        max_records=max_records,\n    )\n'''


def _runtime_template(request: ScaffoldRequest) -> str:
    code = request.jurisdiction.lower()
    return f'''from __future__ import annotations\n\nfrom pathlib import Path\nfrom typing import Mapping\n\nfrom app.trademark_framework.runtime import RuntimeRequest\nfrom app.trademark_jurisdictions.{code}.loader import execute_materialized_source\n\n\nRUNTIME_ADAPTER_ID = "{request.jurisdiction}_TRADEMARK_RUNTIME_V1"\nSOURCE_ID = "{request.source_id}"\n\n\ndef request_from_source(\n    *,\n    jurisdiction: str,\n    source_id: str,\n    path: Path,\n    metadata: Mapping[str, object],\n    max_records: int | None,\n) -> RuntimeRequest:\n    """Normalize a materialized raw object into the shared runtime request contract."""\n    raise NotImplementedError("define {request.jurisdiction} runtime selectors and parser version")\n\n\ndef preflight(request: RuntimeRequest, *, sample_limit: int = 100):\n    """Return the country source's no-write preflight result."""\n    raise NotImplementedError("connect {request.jurisdiction} runtime to preflight")\n\n\ndef execute(request: RuntimeRequest) -> int:\n    """Reuse the generated durable native loader after source-specific contracts are filled."""\n    normalized = request.normalized()\n    result = execute_materialized_source(\n        path=normalized.path,\n        parser_version=normalized.parser_version,\n        metadata=normalized.metadata,\n        max_records=normalized.max_records,\n    )\n    return result.processed_records\n'''


def _current_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\n\nCURRENT_PROJECTION_VERSION = "{request.jurisdiction}_TRADEMARK_CURRENT_V1"\n\n\ndef current_projection_contract() -> dict[str, object]:\n    """Describe source ordering only after baseline/delta/API semantics are proven."""\n    return {{\n        "implemented": False,\n        "ordering_fields": [],\n        "tombstone_supported": False,\n        "reason": "source-current semantics not yet proven",\n    }}\n'''


def _assets_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\n\nASSET_VERSION = "{request.jurisdiction}_TRADEMARK_ASSET_V1"\n\n\ndef asset_contract() -> dict[str, object]:\n    """Describe authority images/media/documents without storing opaque DB blobs by default."""\n    return {{\n        "implemented": False,\n        "object_storage": False,\n        "reason": "source asset semantics not yet profiled",\n    }}\n'''


def _acceptance_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\n\nACCEPTANCE_VERSION = "{request.jurisdiction}_TRADEMARK_ACCEPTANCE_V1"\n\n\ndef jurisdiction_acceptance() -> dict[str, object]:\n    """Country-specific checks layered on generic manifest/replay acceptance.\n\n    Never equate missing source observations with legal nonexistence unless the source\n    contract independently proves trusted-for-silence semantics.\n    """\n    return {{\n        "accepted": False,\n        "trusted_for_silence": False,\n        "reason": "country acceptance rules not implemented",\n    }}\n'''


def _fixture_readme(request: ScaffoldRequest) -> str:
    return f'''# {request.jurisdiction} trademark fixtures\n\nAdd the smallest authority-grounded fixtures needed to prove:\n\n- source identity and native field extraction;\n- acquisition paging/cursor semantics and raw-object materialization when acquisition is remote;\n- source preflight/schema drift detection;\n- reviewed mapping contracts and native-store bundle definitions;\n- deterministic source_index ordering and native-ingest interruption/resume equivalence;\n- runtime selector normalization and pipeline routing;\n- update/delete or snapshot semantics where the source declares them;\n- malformed/unknown source input fails safely;\n- current-state projection never depends on ingestion time;\n- asset/media linkage when the source provides assets;\n- absence of a source record is not promoted to a legal conclusion.\n\nDo not fabricate a production parser/runtime/acquisition adapter from this scaffold without a real\nschema/data dictionary and representative source samples.\n'''


def build_scaffold(request: ScaffoldRequest) -> ScaffoldPlan:
    request = request.normalized()
    code = request.jurisdiction.lower()
    base = f"app/trademark_jurisdictions/{code}"
    files = {
        f"{base}/__init__.py": f'"""{request.jurisdiction} country-native trademark adapter."""\n',
        f"{base}/country.py": _country_template(request),
        f"{base}/acquisition.py": _acquisition_template(request),
        f"{base}/adapter.py": _adapter_template(request),
        f"{base}/mapping.py": _mapping_template(request),
        f"{base}/store.py": _store_template(request),
        f"{base}/schema.py": _schema_template(request),
        f"{base}/preflight.py": _preflight_template(request),
        f"{base}/loader.py": _loader_template(request),
        f"{base}/runtime.py": _runtime_template(request),
        f"{base}/current.py": _current_template(request),
        f"{base}/assets.py": _assets_template(request),
        f"{base}/acceptance.py": _acceptance_template(request),
        f"{base}/fixtures/README.md": _fixture_readme(request),
    }
    return ScaffoldPlan(version=SCAFFOLD_VERSION, request=request, files=files)
