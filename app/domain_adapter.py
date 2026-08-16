from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


DOMAIN_ADAPTER_CONTRACT_VERSION = "MARKORBIT_DOMAIN_ADAPTER_V1"
DOMAIN_LIFECYCLE = (
    "DISCOVER",
    "REGISTER_SOURCE",
    "VERIFY_SOURCE",
    "PARSE",
    "STAGE",
    "NORMALIZE",
    "PUBLISH",
    "EMIT_EVENTS",
    "AUDIT",
    "ACCEPT",
)


@dataclass(frozen=True)
class DomainAdapterDescriptor:
    domain: str
    adapter_version: str
    identity_kind: str
    source_authority: str
    supports_change_feed: bool
    supports_history: bool


class TrademarkDomainAdapter(Protocol):
    """Behavioral contract for future trademark source adapters.

    Implementations may use different parsers and source-specific schemas, but they
    must preserve lifecycle ordering, provenance, fail-closed acceptance, and the
    boundary that source facts are not legal conclusions.
    """

    descriptor: DomainAdapterDescriptor

    def discover_sources(self) -> Any: ...

    def register_source(self, source: Any) -> Any: ...

    def verify_source(self, registered_source: Any) -> Any: ...

    def parse(self, verified_source: Any) -> Any: ...

    def stage(self, parsed_source: Any) -> Any: ...

    def normalize(self, staged_source: Any) -> Any: ...

    def publish(self, normalized_source: Any) -> Any: ...

    def emit_events(self, published_source: Any) -> Any: ...

    def audit(self, published_source: Any) -> Any: ...

    def accept(self, audit_result: Any) -> Any: ...


def domain_adapter_contract() -> dict[str, Any]:
    return {
        "version": DOMAIN_ADAPTER_CONTRACT_VERSION,
        "service_role": "SOURCE_FACT_DOMAIN_ADAPTER",
        "lifecycle": list(DOMAIN_LIFECYCLE),
        "required_invariants": {
            "source_identity_before_mutation": True,
            "source_verification_before_parse": True,
            "deterministic_replay": True,
            "durable_resume_for_large_work": True,
            "provenance_preserved": True,
            "history_does_not_invent_legal_event_time": True,
            "global_envelope_does_not_replace_source_payload": True,
            "publish_before_audit": True,
            "audit_before_accept": True,
            "acceptance_fails_closed": True,
            "consumer_writeback_to_source_facts": False,
            "legal_conclusion": False,
        },
        "adapter_owned": [
            "source_discovery",
            "source_identity_and_verification",
            "source_specific_parser",
            "source_to_fact_mapping",
            "jurisdiction_identity_rules",
            "jurisdiction_semantics_guards",
            "jurisdiction_acceptance_rules",
        ],
        "engine_owned": [
            "durable_work_units",
            "checkpoint_and_resume",
            "bounded_execution",
            "progress_and_failure_telemetry",
            "provenance_contract",
            "global_fact_event_envelope_contract",
            "consumer_service_boundary",
        ],
    }
