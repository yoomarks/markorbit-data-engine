from __future__ import annotations

from typing import Any

from app.integration_contract import (
    CONTRACT_VERSION,
    CONTRACT_VERSION_HEADER,
    REQUEST_ID_HEADER,
    SOURCE_OWNER,
    SOURCE_OWNER_HEADER,
)

CORRELATION_ID_HEADER = "x-correlation-id"


def g0_contract_descriptor() -> dict[str, Any]:
    return {
        "contract_id": CONTRACT_VERSION,
        "source_owner": SOURCE_OWNER,
        "compatibility": {
            "v1_default": "additive",
            "breaking_change_policy": "cross_repo_migration_or_new_version",
            "deprecation_policy": "no_v1_removal_without_cross_repo_review",
        },
        "query_contract": {
            "methods": ["GET"],
            "storage_independent": True,
            "resources": [
                {"path": "/api/v1/contract", "query": {}, "pagination": "none"},
                {"path": "/api/v1/health", "query": {}, "pagination": "none"},
                {
                    "path": "/api/v1/owner-summary",
                    "query": {},
                    "pagination": "none",
                    "semantics": "bounded_owner_local_aggregate_projection",
                    "read_only": True,
                    "admin_detail_exposed": False,
                },
                {"path": "/api/v1/cn/cases/{application_number}", "query": {}, "pagination": "none"},
                {
                    "path": "/api/v1/cn/discovery/preliminary-publications",
                    "query": {
                        "application_number_start": {"type": "string", "required": True, "semantics": "inclusive"},
                        "application_number_end": {"type": "string", "required": True, "semantics": "exclusive"},
                        "page_size": {"type": "integer", "default": 50, "min": 1, "max": 100},
                        "cursor": {"type": "opaque_string", "required": False, "max_length": 8192},
                    },
                    "pagination": "bounded_keyset_cursor",
                    "snapshot": "CN_QUIESCENT_SERVING_EPOCH",
                    "hard_bounds": {"max_pages": 10, "max_results": 1000},
                    "read_budget": {
                        "max_rows_to_read": 250000,
                        "max_bytes_to_read": 268435456,
                        "overflow_mode": "throw",
                    },
                    "candidate_semantics": "objective_preliminary_publication_fact_only",
                    "business_state_owned_outside_data_engine": True,
                },
                {"path": "/api/v1/us/cases/{serial_number}", "query": {}, "pagination": "none"},
                {
                    "path": "/api/v1/us/cases/{serial_number}/360",
                    "query": {
                        "as_of": {"type": "date", "required": False},
                        "history_limit": {"type": "integer", "default": 500, "min": 1, "max": 5000},
                        "assignment_limit": {"type": "integer", "default": 100, "min": 1, "max": 500},
                        "ttab_limit": {"type": "integer", "default": 100, "min": 1, "max": 500},
                    },
                    "pagination": "bounded_limit_no_cursor",
                },
                {
                    "path": "/api/v1/us/cases/{serial_number}/history",
                    "query": {"limit": {"type": "integer", "default": 500, "min": 1, "max": 5000}},
                    "pagination": "bounded_limit_no_cursor",
                },
                {
                    "path": "/api/v1/us/cases/{serial_number}/assignments",
                    "query": {"limit": {"type": "integer", "default": 100, "min": 1, "max": 500}},
                    "pagination": "bounded_limit_no_cursor",
                },
                {
                    "path": "/api/v1/us/cases/{serial_number}/ttab",
                    "query": {"limit": {"type": "integer", "default": 100, "min": 1, "max": 500}},
                    "pagination": "bounded_limit_no_cursor",
                },
                {
                    "path": "/api/v1/us/changes",
                    "query": {
                        "after_source_rank": {"type": "integer", "default": 0, "min": 0},
                        "after_serial": {"type": "string", "default": ""},
                        "scan_limit": {"type": "integer", "default": 200, "min": 1, "max": 1000},
                    },
                    "pagination": "provider_change_cursor",
                },
            ],
        },
        "fact_semantics": {
            "observed": "resource/fact was observed and returned by the provider",
            "not_found": "requested resource key is absent from the current provider read model; this does not prove coverage",
            "not_covered": "reserved explicit state; never infer from 404 or service failure",
            "no_observation": "reserved explicit state for a covered scope with no observation; never infer from empty transport response",
            "tombstone": "reserved explicit state only when provider has durable deletion/supersession evidence",
            "service_unavailable": "runtime/provider failure; never convert to factual absence",
            "current_explicit_states": ["observed", "not_found", "service_unavailable"],
            "reserved_not_yet_emitted": ["not_covered", "no_observation", "tombstone"],
        },
        "security": {
            "scheme": "BEARER_API_KEY",
            "authorization_header": "Authorization: Bearer <key>",
            "g1_target_mode": "required",
            "secret_owner": "Data Engine deployment secret store; matching consumer credential in MarkOrbit secret store",
            "environment_isolation": True,
            "minimum_key_length": 32,
            "multi_key_rotation": True,
            "revocation": "remove retired key after overlap window",
            "tls": "required across non-loopback/shared service boundaries",
            "unauthenticated_status": 401,
            "forbidden_status": 403,
            "forbidden_current_behavior": "reserved; V1 has no scope/role authorization layer",
        },
        "tracing": {
            "request_id_header": REQUEST_ID_HEADER,
            "correlation_id_header": CORRELATION_ID_HEADER,
            "generation": "accept valid incoming IDs; otherwise generate request ID and default correlation ID to request ID",
            "forwarding": "consumers forward correlation ID across service hops and may provide a hop-specific request ID",
            "response_echo": [REQUEST_ID_HEADER, CORRELATION_ID_HEADER, CONTRACT_VERSION_HEADER, SOURCE_OWNER_HEADER],
            "provider_trace_identifier": REQUEST_ID_HEADER,
        },
        "runtime_errors": {
            "schema": {"required": ["code", "message", "retryable"], "optional": ["detail", "fact_state"]},
            "status_codes": {
                "400": {"retryable": False, "meaning": "invalid query or malformed request"},
                "401": {"retryable": False, "meaning": "missing or invalid service credential"},
                "403": {"retryable": False, "meaning": "authenticated but forbidden; reserved until authorization scopes exist"},
                "404": {"retryable": False, "meaning": "resource not found; coverage remains unknown unless separately proven"},
                "409": {"retryable": False, "meaning": "explicit contract/version conflict"},
                "429": {"retryable": True, "meaning": "provider backpressure; obey Retry-After"},
                "503": {"retryable": True, "meaning": "provider/dependency unavailable or invalid required auth configuration"},
            },
            "timeout": "consumer/network timeout is retryable and must never be converted to a factual negative",
            "schema_mismatch": "consumer fails closed when contract_version differs from the supported contract",
        },
        "rate_limit": {
            "server_enforcement_default": False,
            "enabled_config": "INTEGRATION_RATE_LIMIT_ENABLED",
            "default_max_requests": 120,
            "default_window_seconds": 60,
            "subject": "source IP per provider process",
            "throttled_status": 429,
            "retry_after_header": "Retry-After",
            "consumer_rule": "honor 429 and Retry-After even when a deployment changes the configured envelope",
        },
    }
