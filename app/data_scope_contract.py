"""M20 data-use semantics; this descriptor does not evaluate or issue grants."""

from __future__ import annotations

from typing import Any


DATA_SCOPE_CONTRACT_VERSION = "MARKORBIT_DATA_USE_SCOPE_DESCRIPTOR_V1"


def data_scope_contract() -> dict[str, Any]:
    return {
        "contract_version": DATA_SCOPE_CONTRACT_VERSION,
        "status": "METADATA_ONLY",
        "runtime_enforcement_implemented": False,
        "descriptor_grants_access": False,
        "scope_dimensions": [
            "jurisdiction",
            "dataset_family_version",
            "access_action",
            "history_freshness",
            "purpose_product_context",
        ],
        "access_actions": [
            "LOOKUP",
            "SEARCH",
            "AGGREGATE",
            "WATCH",
            "EXPORT",
            "API",
            "OPPORTUNITY_USE",
            "CAPABILITY_USE",
        ],
        "priority_data_linked_jurisdictions": ["CN", "US", "CA", "GB", "EU", "AU", "NZ", "SG"],
        "priority_jurisdiction_implies_dataset_availability": False,
        "authorization_boundary": {
            "entitlement_owner": "CORE",
            "reuse_contracts": ["EntitlementGrantV1", "ResolvedEntitlementV1"],
            "data_engine_owns_parallel_grant_store": False,
            "service_authentication_is_data_use_authorization": False,
            "workspace_read_permission_is_data_use_authorization": False,
            "lookup_or_search_implies_opportunity_use": False,
            "lookup_or_search_implies_publication_permission": False,
            "creator_access_action": "CAPABILITY_USE",
            "creator_product_context_required": True,
            "protected_action_authorized_by_data_access": False,
        },
        "admission_requirements": {
            "status": "REQUIRED_FOR_FUTURE_RUNTIME_ADMISSION",
            "deny_by_default": True,
            "trusted_server_side_subject_and_purpose": True,
            "current_core_entitlement_evaluation": True,
            "exact_dataset_and_source_version": True,
            "source_license_and_permitted_use": True,
            "data_readiness_and_currentness": True,
            "unknown_license_or_grant_is_permission": False,
            "source_authority_is_use_license": False,
            "pipeline_readiness_is_production_acceptance": False,
            "data_trust_is_workspace_entitlement": False,
        },
        "capability_dependency_requirements": {
            "required_declaration": "requiredDataScopes",
            "optional_declaration": "optionalDataScopes",
            "missing_required": "DISABLE_OR_MANUAL_HANDOFF",
            "missing_optional": "EXPLICIT_REDUCED_MODE",
            "reduced_mode_preserves_full_confidence": False,
        },
        "dataset_metadata_requirements": [
            "source_and_provenance",
            "coverage_and_observation_count",
            "source_as_of_and_currentness",
            "maintenance_plan_and_cost",
            "license_and_permitted_use",
            "capability_dependencies",
        ],
        "commercial_boundary": {
            "dataset_taxonomy_and_pack_allocation_frozen": False,
            "prices_and_refresh_slas_frozen": False,
            "complete_scope_cartesian_product_required": False,
        },
    }
