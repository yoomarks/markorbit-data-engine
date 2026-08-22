from __future__ import annotations

import ast

from app.trademark_framework.contracts import (
    DataFormat,
    SourceAdapterKind,
    TransportKind,
    UpdateSemantics,
)
from app.trademark_framework.scaffold import SCAFFOLD_VERSION, ScaffoldRequest, build_scaffold


def _assert_common_v5_contract(plan) -> None:
    assert plan.version == "TRADEMARK_COUNTRY_SCAFFOLD_V5" == SCAFFOLD_VERSION
    code = plan.request.jurisdiction.lower()
    base = f"app/trademark_jurisdictions/{code}"
    required = {
        f"{base}/__init__.py",
        f"{base}/country.py",
        f"{base}/acquisition.py",
        f"{base}/adapter.py",
        f"{base}/mapping.py",
        f"{base}/store.py",
        f"{base}/schema.py",
        f"{base}/preflight.py",
        f"{base}/loader.py",
        f"{base}/runtime.py",
        f"{base}/current.py",
        f"{base}/assets.py",
        f"{base}/acceptance.py",
        f"{base}/fixtures/README.md",
    }
    assert required.issubset(plan.files)

    for relative_path, source in plan.files.items():
        if relative_path.endswith(".py"):
            ast.parse(source, filename=relative_path)

    country = plan.files[f"{base}/country.py"]
    mapping = plan.files[f"{base}/mapping.py"]
    store = plan.files[f"{base}/store.py"]
    schema = plan.files[f"{base}/schema.py"]
    loader = plan.files[f"{base}/loader.py"]
    runtime = plan.files[f"{base}/runtime.py"]

    assert "pipeline_ready=False" in country
    assert "TODO_SOURCE_IDENTITY" in country
    assert "MappingContract" in mapping
    assert "build_mapping_contracts" in mapping
    assert "NativeStoreBundle" in store
    assert "build_native_store_bundle" in store
    assert "NotImplementedError" in store
    assert "install_native_store_bundle" in schema
    assert "execute_native_ingest" in loader
    assert "NativeRecordEnvelope" in loader
    assert "resolve_pipeline_id" in loader
    assert "execute_materialized_source" in runtime


def test_http_scaffold_v5_reuses_shared_acquisition_and_native_ingest_stack() -> None:
    plan = build_scaffold(
        ScaffoldRequest(
            jurisdiction="KR",
            source_id="KIPO_OFFICIAL_FIXTURE",
            adapter_kind=SourceAdapterKind.REST_API,
            data_format=DataFormat.JSON,
            update_semantics=UpdateSemantics.API_CURRENT,
            transport=TransportKind.HTTP_API,
        )
    )
    _assert_common_v5_contract(plan)

    acquisition = plan.files["app/trademark_jurisdictions/kr/acquisition.py"]
    assert "HttpPaginatedAcquisitionAdapter" in acquisition
    assert "PageNumberPagination" in acquisition
    assert "OffsetLimitPagination" in acquisition
    assert "OpaqueCursorPagination" in acquisition


def test_file_scaffold_v5_keeps_file_acquisition_without_http_assumptions() -> None:
    plan = build_scaffold(
        ScaffoldRequest(
            jurisdiction="JP",
            source_id="JPO_OFFICIAL_FIXTURE",
            adapter_kind=SourceAdapterKind.ZIP_XML,
            data_format=DataFormat.XML,
            update_semantics=UpdateSemantics.SNAPSHOT,
            transport=TransportKind.FILE,
        )
    )
    _assert_common_v5_contract(plan)

    acquisition = plan.files["app/trademark_jurisdictions/jp/acquisition.py"]
    assert "AcquisitionPageRequest" in acquisition
    assert "HttpPaginatedAcquisitionAdapter" not in acquisition
