from __future__ import annotations

from app.trademark_framework.contracts import (
    DataFormat,
    SourceAdapterKind,
    TransportKind,
    UpdateSemantics,
)
from app.trademark_framework.scaffold import (
    SCAFFOLD_VERSION,
    ScaffoldPlan,
    ScaffoldRequest,
    build_scaffold,
)


FACTORY_SCAFFOLD_VERSION = "TRADEMARK_COUNTRY_FACTORY_SCAFFOLD_V1"


def build_country_scaffold(
    *,
    jurisdiction: str,
    source_id: str,
    adapter_kind: SourceAdapterKind,
    data_format: DataFormat,
    update_semantics: UpdateSemantics,
    transport: TransportKind,
    store_schema: str | None = None,
) -> ScaffoldPlan:
    """Build the authoritative framework scaffold through the factory facade.

    The factory deliberately does not maintain another generator/template tree. This keeps
    new-country scaffolding aligned with the same acquisition/runtime/current/acceptance
    contracts used by existing jurisdictions.
    """
    return build_scaffold(
        ScaffoldRequest(
            jurisdiction=jurisdiction,
            source_id=source_id,
            adapter_kind=adapter_kind,
            data_format=data_format,
            update_semantics=update_semantics,
            transport=transport,
            store_schema=store_schema,
        )
    )


__all__ = [
    "FACTORY_SCAFFOLD_VERSION",
    "SCAFFOLD_VERSION",
    "ScaffoldPlan",
    "ScaffoldRequest",
    "build_country_scaffold",
]
