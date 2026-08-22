import ast

from app.trademark_framework.contracts import (
    DataFormat,
    SourceAdapterKind,
    TransportKind,
    UpdateSemantics,
)
from app.trademark_framework.scaffold import SCAFFOLD_VERSION, ScaffoldRequest, build_scaffold


def _plan(*, transport: TransportKind, adapter: SourceAdapterKind):
    return build_scaffold(
        ScaffoldRequest(
            jurisdiction="JP",
            source_id="JPO_EXAMPLE",
            adapter_kind=adapter,
            data_format=DataFormat.JSON if transport == TransportKind.HTTP_API else DataFormat.XML,
            update_semantics=(
                UpdateSemantics.API_CURRENT
                if transport == TransportKind.HTTP_API
                else UpdateSemantics.SNAPSHOT
            ),
            transport=transport,
        )
    )


def test_scaffold_v5_generates_native_store_construction_path_without_guessing() -> None:
    plan = _plan(transport=TransportKind.HTTP_API, adapter=SourceAdapterKind.REST_API)
    assert plan.version == SCAFFOLD_VERSION == "TRADEMARK_COUNTRY_SCAFFOLD_V5"
    assert len(plan.files) == 13

    base = "app/trademark_jurisdictions/jp"
    country = plan.files[f"{base}/country.py"]
    mapping = plan.files[f"{base}/mapping.py"]
    schema = plan.files[f"{base}/schema.py"]
    store = plan.files[f"{base}/store.py"]
    acquisition = plan.files[f"{base}/acquisition.py"]

    assert "pipeline_ready=False" in country
    assert "TODO_SOURCE_IDENTITY" in country
    assert "def mapping_contracts()" in mapping
    assert "return ()" in mapping
    assert "def observation_table_specs()" in schema
    assert "return ()" in schema
    assert "NativeStoreBundle" in store
    assert "StoreBinding" in store
    assert "install_native_store_bundle" in store
    assert "append_native_record_bundle" in store
    assert "NotImplementedError" in store
    assert "HttpPaginatedAcquisitionAdapter" in acquisition

    for path, content in plan.files.items():
        if path.endswith(".py"):
            ast.parse(content, filename=path)


def test_scaffold_v5_preserves_file_source_acquisition_pattern() -> None:
    plan = _plan(transport=TransportKind.FILE, adapter=SourceAdapterKind.ZIP_XML)
    acquisition = plan.files["app/trademark_jurisdictions/jp/acquisition.py"]
    assert "AcquisitionPageRequest" in acquisition
    assert "HttpPaginatedAcquisitionAdapter" not in acquisition
    assert "app/trademark_jurisdictions/jp/store.py" in plan.files


def test_scaffold_v5_contains_no_guessed_native_trademark_fields() -> None:
    plan = _plan(transport=TransportKind.HTTP_API, adapter=SourceAdapterKind.REST_API)
    generated_store_contract = "\n".join(
        plan.files[path]
        for path in (
            "app/trademark_jurisdictions/jp/mapping.py",
            "app/trademark_jurisdictions/jp/schema.py",
            "app/trademark_jurisdictions/jp/store.py",
        )
    )
    for guessed_field in (
        "registration_number",
        "owner_name",
        "status_code",
        "renewal_date",
        "is_dead",
        "brand_family",
    ):
        assert guessed_field not in generated_store_contract
