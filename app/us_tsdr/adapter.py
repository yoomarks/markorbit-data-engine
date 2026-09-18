from __future__ import annotations

from app.domain_adapter import DomainAdapterDescriptor
from app.us_tsdr.intent import INTENT_CONTRACT_VERSION


TSDR_SOURCE_VERSION = "US_TSDR_SOURCE_V2"
TSDR_SOURCE_SEMANTICS = (
    "USPTO_TSDR_SPARSE_VERIFICATION_AND_ENRICHMENT_NOT_PRIMARY_US_TRADEMARK_DATA_SOURCE"
)

DESCRIPTOR = DomainAdapterDescriptor(
    domain="US_TSDR",
    adapter_version=TSDR_SOURCE_VERSION,
    identity_kind="US_SERIAL_NUMBER",
    source_authority="USPTO_TSDR",
    supports_change_feed=False,
    supports_history=True,
)


def source_contract() -> dict[str, object]:
    return {
        "source": "US_TSDR",
        "authority": "USPTO_TSDR",
        "source_version": TSDR_SOURCE_VERSION,
        "acquisition_intent_contract_version": INTENT_CONTRACT_VERSION,
        "acquisition_mode": "SPARSE_INTENT_DRIVEN",
        "primary_structured_source": False,
        "primary_structured_source_note": (
            "USPTO daily XML remains the primary structured US trademark source"
        ),
        "resources": {
            "STATUS_CONTACT": "VERIFY_CURRENT_STATUS_AND_PUBLIC_CONTACT_FACTS",
            "DOCUMENT": "TARGETED_BUSINESS_CHAIN_OR_CASE_RESEARCH_EVIDENCE",
            "LOGO": "ONE_SHOT_TRADEMARK_ASSET_WITH_EXPLICIT_EXCEPTION_REFRESH",
        },
        "collector_decides_priority": False,
        "data_engine_decides_acquisition_need": False,
        "acquisition_need_owner": "UPSTREAM_CAPABILITY_OR_PRODUCT",
        "data_engine_role": "FACTS_AND_BOUNDED_ACQUISITION_EXECUTION_ONLY",
        "opportunity_discovery_in_data_engine": False,
        "legacy_weekly_policy": "DEPRECATED_COMPATIBILITY_ONLY_NOT_OPPORTUNITY_AUTHORITY",
    }
