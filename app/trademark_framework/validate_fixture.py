from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from app.global_trademarks.catalog import country_plan
from app.trademark_framework.contracts import (
    CurrentProjectionMode,
    DataFormat,
    JurisdictionStage,
    SourceAdapterKind,
    TransportKind,
    UpdateSemantics,
)
from app.trademark_framework.registry import (
    FRAMEWORK_VERSION,
    country_pack,
    country_packs,
    framework_audit,
    resolve_pipeline_id,
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
    assert country_pack("US").maturity == JurisdictionStage.PRODUCTION_CURRENT

    ca = country_pack("CA")
    assert ca.maturity == JurisdictionStage.CURRENT_PROJECTION_READY
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
    assert resolve_pipeline_id("CA", "CIPO_GLOBAL_2025_06_14", {}) == "CIPO_ST96_CORE_V1"

    au = country_pack("AU")
    assert au.maturity == JurisdictionStage.COUNTRY_STORE_READY
    assert au.identity.fields == ("application_number", "ip_right_type")
    assert au.source("IPGOD_2022").adapter_kind == SourceAdapterKind.MULTI_TABLE_FILES
    assert len(au.source("IPGOD_2022").pipeline_ids) == 6
    assert (
        resolve_pipeline_id("AU", "IPGOD_2022", {"source_table": "application-events"})
        == "IPGOD_2022_APPLICATION_EVENTS_V1"
    )
    assert resolve_pipeline_id("AU", "IPGOD_2022", {"source_table": "unknown"}) is None

    gb = country_pack("GB")
    assert gb.maturity == JurisdictionStage.COUNTRY_STORE_READY
    assert gb.source("UKIPO_OPEN_DATA_2018").adapter_kind == SourceAdapterKind.DELIMITED_FILE
    assert gb.source("UKIPO_OPEN_DATA_2018").data_format == DataFormat.TXT
    assert len(gb.source("UKIPO_OPEN_DATA_2018").pipeline_ids) == 2
    assert (
        resolve_pipeline_id(
            "GB",
            "UKIPO_OPEN_DATA_2018",
            {"source_stream": "DOMESTIC"},
        )
        == "UKIPO_2018_DOMESTIC_V1"
    )
    assert (
        resolve_pipeline_id(
            "UK",
            "UKIPO_OPEN_DATA_2018",
            {"source_stream": "MADRID_IR"},
        )
        == "UKIPO_2018_MADRID_IR_V1"
    )

    eu = country_pack("EU")
    nz = country_pack("NZ")
    assert eu.current_projection.mode == CurrentProjectionMode.HISTORICAL_ONLY
    assert nz.current_projection.mode == CurrentProjectionMode.HISTORICAL_ONLY
    assert len(eu.source("TM_LINK_EU").pipeline_ids) == 4
    assert len(nz.source("TM_LINK_NZ").pipeline_ids) == 4
    assert (
        resolve_pipeline_id("EM", "TM_LINK_EU", {"source_table": "details"})
        == "TM_LINK_EU_TRADEMARK_DETAILS_V1"
    )
    assert (
        resolve_pipeline_id("NZ", "TM_LINK_NZ", {"source_table": "classes"})
        == "TM_LINK_NZ_NICE_CLASS_V1"
    )

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
    assert len(plan.files) == 10
    expected_paths = {
        "app/trademark_jurisdictions/jp/country.py",
        "app/trademark_jurisdictions/jp/adapter.py",
        "app/trademark_jurisdictions/jp/mapping.py",
        "app/trademark_jurisdictions/jp/schema.py",
        "app/trademark_jurisdictions/jp/preflight.py",
        "app/trademark_jurisdictions/jp/current.py",
        "app/trademark_jurisdictions/jp/assets.py",
        "app/trademark_jurisdictions/jp/acceptance.py",
    }
    assert expected_paths.issubset(plan.files)
    country_source = plan.files["app/trademark_jurisdictions/jp/country.py"]
    assert "pipeline_ready=False" in country_source
    assert "TODO_SOURCE_IDENTITY" in country_source
    assert "NotImplementedError" in plan.files["app/trademark_jurisdictions/jp/preflight.py"]
    assert "trusted_for_silence" in plan.files["app/trademark_jurisdictions/jp/acceptance.py"]

    for relative_path, content in plan.files.items():
        if relative_path.endswith(".py"):
            ast.parse(content, filename=relative_path)

    country_namespace: dict[str, object] = {}
    exec(compile(country_source, "generated-country.py", "exec"), country_namespace)
    generated_pack = country_namespace["COUNTRY_PACK"]
    assert generated_pack.maturity == JurisdictionStage.SOURCE_FOUND
    assert generated_pack.source("JPO_OFFICIAL_BULK").pipeline_ready is False

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
            "maturity": {pack.jurisdiction: pack.maturity.value for pack in country_packs()},
            "catalog_single_source_of_truth": True,
            "pipeline_routing_centralized": True,
            "country_scaffold_version": SCAFFOLD_VERSION,
            "scaffold_file_count": len(plan.files),
            "scaffold_default_maturity": generated_pack.maturity.value,
            "scaffold_default_pipeline_ready": False,
            "scaffold_overwrite_blocked": True,
            "db_writes": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
