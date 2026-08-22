from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from app.global_trademarks.catalog import country_plan
from app.trademark_framework.contracts import (
    CurrentProjectionMode,
    DataFormat,
    SourceAdapterKind,
    TransportKind,
    UpdateSemantics,
)
from app.trademark_framework.registry import (
    FRAMEWORK_VERSION,
    country_pack,
    country_packs,
    framework_audit,
)
from app.trademark_framework.scaffold import SCAFFOLD_VERSION, ScaffoldRequest, build_scaffold


def main() -> int:
    audit = framework_audit()
    assert audit.ready, audit.errors
    assert audit.framework_version == FRAMEWORK_VERSION
    assert audit.country_count == 6
    assert audit.source_count == 17
    assert audit.ready_source_count == 6

    assert country_pack("EM").jurisdiction == "EU"
    assert country_pack("UK").jurisdiction == "GB"

    ca = country_pack("CA")
    assert ca.identity.fields == ("application_number", "extension_counter")
    assert ca.current_projection.mode == CurrentProjectionMode.MANIFEST_ORDERED
    assert ca.current_projection.ordering_fields == (
        "source_period_end",
        "source_precedence",
        "source_sequence",
    )
    assert ca.current_projection.tombstone_supported is True
    assert ca.source("CIPO_GLOBAL_2025_06_14").pipeline_ready is True
    assert ca.source("CIPO_WEEKLY").update_semantics == UpdateSemantics.UPDATE_DELETE
    assert ca.source("CIPO_WEEKLY").pipeline_ready is False

    au = country_pack("AU")
    assert au.identity.fields == ("application_number", "ip_right_type")
    assert au.source("IPGOD_2022").adapter_kind == SourceAdapterKind.MULTI_TABLE_FILES
    assert len(au.source("IPGOD_2022").pipeline_ids) == 6

    gb = country_pack("GB")
    assert gb.source("UKIPO_OPEN_DATA_2018").adapter_kind == SourceAdapterKind.DELIMITED_FILE
    assert gb.source("UKIPO_OPEN_DATA_2018").data_format == DataFormat.TXT
    assert len(gb.source("UKIPO_OPEN_DATA_2018").pipeline_ids) == 2

    eu = country_pack("EU")
    nz = country_pack("NZ")
    assert eu.current_projection.mode == CurrentProjectionMode.HISTORICAL_ONLY
    assert nz.current_projection.mode == CurrentProjectionMode.HISTORICAL_ONLY
    assert len(eu.source("TM_LINK_EU").pipeline_ids) == 4
    assert len(nz.source("TM_LINK_NZ").pipeline_ids) == 4

    # The old global-trademark catalog is now a compatibility view, not a second
    # manually maintained source registry.
    for pack in country_packs():
        compat = country_plan(pack.jurisdiction)
        assert compat.jurisdiction == pack.jurisdiction
        assert compat.store_schema == pack.store_schema
        assert tuple(source.source_id for source in compat.sources) == tuple(
            source.source_id for source in pack.sources
        )
        assert tuple(source.pipeline_ready for source in compat.sources) == tuple(
            source.pipeline_ready for source in pack.sources
        )

    request = ScaffoldRequest(
        jurisdiction="JP",
        source_id="JPO_OFFICIAL_BULK",
        adapter_kind=SourceAdapterKind.ZIP_XML,
        data_format=DataFormat.XML,
        update_semantics=UpdateSemantics.SNAPSHOT,
        transport=TransportKind.FILE,
    )
    plan = build_scaffold(request)
    assert plan.version == SCAFFOLD_VERSION
    assert plan.request.store_schema == "trademark_jp"
    assert len(plan.files) == 6
    assert "app/trademark_jurisdictions/jp/country.py" in plan.files
    assert "pipeline_ready=False" in plan.files["app/trademark_jurisdictions/jp/country.py"]
    assert "TODO_SOURCE_IDENTITY" in plan.files["app/trademark_jurisdictions/jp/country.py"]

    for relative_path, content in plan.files.items():
        if relative_path.endswith(".py"):
            ast.parse(content, filename=relative_path)

    with tempfile.TemporaryDirectory(prefix="trademark-country-scaffold-") as temporary:
        root = Path(temporary)
        written = plan.write(root)
        assert len(written) == len(plan.files)
        assert all(path.exists() for path in written)
        overwrite_blocked = False
        try:
            plan.write(root)
        except FileExistsError:
            overwrite_blocked = True
        assert overwrite_blocked is True

    print(
        {
            "status": "PASS",
            "framework_version": FRAMEWORK_VERSION,
            "country_patterns_validated": [pack.jurisdiction for pack in country_packs()],
            "catalog_single_source_of_truth": True,
            "country_scaffold_version": SCAFFOLD_VERSION,
            "scaffold_default_pipeline_ready": False,
            "scaffold_overwrite_blocked": True,
            "db_writes": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
