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


SCAFFOLD_VERSION = "TRADEMARK_COUNTRY_SCAFFOLD_V1"
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
    code = request.jurisdiction.lower()
    return f'''from __future__ import annotations\n\nfrom collections.abc import Iterator\nfrom pathlib import Path\n\n\nADAPTER_VERSION = "{request.jurisdiction}_TRADEMARK_ADAPTER_V1"\nSOURCE_ID = "{request.source_id}"\n\n\ndef iter_native_records(path: Path) -> Iterator[dict[str, object]]:\n    """Parse source-native records without inventing semantic/legal facts.\n\n    Replace this stub only after reviewing the authority's schema/data dictionary and\n    real sample payloads. Preserve unknown source fields in source_payload when useful.\n    """\n    raise NotImplementedError("implement {request.jurisdiction} source-native parser")\n\n\ndef native_record_key(record: dict[str, object]) -> str:\n    """Return the source-declared record identity for deterministic replay."""\n    raise NotImplementedError("define {request.jurisdiction} source identity")\n\n\ndef source_operation(record: dict[str, object]) -> str:\n    """Return source operation semantics such as Update/Delete when explicitly declared."""\n    return "OBSERVE"\n'''


def _country_template(request: ScaffoldRequest) -> str:
    return f'''from app.trademark_framework.contracts import (\n    AssetMode,\n    CountryPack,\n    CurrentProjectionContract,\n    CurrentProjectionMode,\n    DataFormat,\n    IdentityContract,\n    ObservationDomain,\n    SourceAdapterKind,\n    SourceDescriptor,\n    SourceRole,\n    TransportKind,\n    UpdateSemantics,\n)\n\n\nCOUNTRY_PACK = CountryPack(\n    jurisdiction="{request.jurisdiction}",\n    store_schema="{request.store_schema}",\n    identity=IdentityContract(\n        fields=("TODO_SOURCE_IDENTITY",),\n        notes="Replace only with source-declared identity after schema review.",\n    ),\n    observation_domains=(ObservationDomain.RECORD,),\n    current_projection=CurrentProjectionContract(\n        mode=CurrentProjectionMode.NOT_IMPLEMENTED,\n        notes="Do not promote current-state semantics until source ordering is proven.",\n    ),\n    asset_mode=AssetMode.NOT_IMPLEMENTED,\n    sources=(\n        SourceDescriptor(\n            source_id="{request.source_id}",\n            role=SourceRole.PRIMARY,\n            authoritative=True,\n            active_now=True,\n            pipeline_ready=False,\n            adapter_kind=SourceAdapterKind.{request.adapter_kind.name},\n            transport=TransportKind.{request.transport.name},\n            data_format=DataFormat.{request.data_format.name},\n            update_semantics=UpdateSemantics.{request.update_semantics.name},\n            notes="Generated scaffold; profile real source before enabling pipeline_ready.",\n        ),\n    ),\n)\n'''


def _schema_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\n\nSCHEMA_VERSION = "{request.jurisdiction}_TRADEMARK_SCHEMA_V1"\nSTORE_SCHEMA = "{request.store_schema}"\n\n\ndef schema_sql() -> str:\n    """Return additive source-native DDL after the source schema has been profiled.\n\n    Keep country-native richness. Do not reduce the source to a global common table.\n    """\n    raise NotImplementedError("design {request.jurisdiction} source-native schema")\n'''


def _acceptance_template(request: ScaffoldRequest) -> str:
    return f'''from __future__ import annotations\n\n\nACCEPTANCE_VERSION = "{request.jurisdiction}_TRADEMARK_ACCEPTANCE_V1"\n\n\ndef jurisdiction_acceptance() -> dict[str, object]:\n    """Country-specific checks layered on generic manifest/replay acceptance.\n\n    Never equate missing source observations with legal nonexistence unless the source\n    contract independently proves trusted-for-silence semantics.\n    """\n    return {{\n        "accepted": False,\n        "trusted_for_silence": False,\n        "reason": "country acceptance rules not implemented",\n    }}\n'''


def _fixture_readme(request: ScaffoldRequest) -> str:
    return f'''# {request.jurisdiction} trademark fixtures\n\nAdd the smallest authority-grounded fixtures needed to prove:\n\n- source identity and native field extraction;\n- replay idempotency;\n- interruption/resume equivalence;\n- update/delete or snapshot semantics where the source declares them;\n- malformed/unknown source input fails safely;\n- current-state projection never depends on ingestion time;\n- absence of a source record is not promoted to a legal conclusion.\n\nDo not fabricate a production parser from this scaffold without a real schema/data dictionary\nand representative source samples.\n'''


def build_scaffold(request: ScaffoldRequest) -> ScaffoldPlan:
    request = request.normalized()
    code = request.jurisdiction.lower()
    base = f"app/trademark_jurisdictions/{code}"
    files = {
        f"{base}/__init__.py": f'"""{request.jurisdiction} country-native trademark adapter."""\n',
        f"{base}/country.py": _country_template(request),
        f"{base}/adapter.py": _adapter_template(request),
        f"{base}/schema.py": _schema_template(request),
        f"{base}/acceptance.py": _acceptance_template(request),
        f"{base}/fixtures/README.md": _fixture_readme(request),
    }
    return ScaffoldPlan(version=SCAFFOLD_VERSION, request=request, files=files)
