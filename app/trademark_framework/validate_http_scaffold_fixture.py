from __future__ import annotations

import ast

from app.trademark_framework.contracts import (
    DataFormat,
    SourceAdapterKind,
    TransportKind,
    UpdateSemantics,
)
from app.trademark_framework.scaffold import SCAFFOLD_VERSION, ScaffoldRequest, build_scaffold


def main() -> int:
    http_plan = build_scaffold(
        ScaffoldRequest(
            jurisdiction="KR",
            source_id="KIPO_OFFICIAL_API",
            adapter_kind=SourceAdapterKind.REST_API,
            data_format=DataFormat.JSON,
            update_semantics=UpdateSemantics.API_CURRENT,
            transport=TransportKind.HTTP_API,
        )
    )
    assert http_plan.version == SCAFFOLD_VERSION == "TRADEMARK_COUNTRY_SCAFFOLD_V4"
    acquisition_path = "app/trademark_jurisdictions/kr/acquisition.py"
    http_source = http_plan.files[acquisition_path]
    assert "HttpPaginatedAcquisitionAdapter" in http_source
    assert "HttpPageInterpretation" in http_source
    assert "PageNumberPagination" in http_source
    assert "OffsetLimitPagination" in http_source
    assert "OpaqueCursorPagination" in http_source
    assert "runtime_headers" in http_source
    assert "runtime_query" in http_source
    assert "interpret_page" in http_source
    assert "NotImplementedError" in http_source
    assert "Bearer" not in http_source
    assert "api_key=" not in http_source.lower()
    ast.parse(http_source, filename=acquisition_path)

    file_plan = build_scaffold(
        ScaffoldRequest(
            jurisdiction="JP",
            source_id="JPO_OFFICIAL_BULK",
            adapter_kind=SourceAdapterKind.ZIP_XML,
            data_format=DataFormat.XML,
            update_semantics=UpdateSemantics.SNAPSHOT,
            transport=TransportKind.FILE,
        )
    )
    file_source = file_plan.files["app/trademark_jurisdictions/jp/acquisition.py"]
    assert "AcquisitionPageRequest" in file_source
    assert "HttpPaginatedAcquisitionAdapter" not in file_source
    assert "Never include API keys" in file_source
    assert "NotImplementedError" in file_source
    ast.parse(file_source, filename="app/trademark_jurisdictions/jp/acquisition.py")

    print(
        {
            "status": "PASS",
            "scaffold_version": SCAFFOLD_VERSION,
            "http_api_scaffold_reuses_shared_bridge": True,
            "http_pagination_not_guessed": True,
            "http_runtime_secret_stub_only": True,
            "file_scaffold_backward_compatible": True,
            "pipeline_ready_promoted": False,
            "network_used": False,
            "database_writes": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
