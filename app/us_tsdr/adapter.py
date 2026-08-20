from __future__ import annotations

from app.domain_adapter import DomainAdapterDescriptor
from app.us_tsdr.policy import POLICY_VERSION


TSDR_SOURCE_VERSION = "US_TSDR_SOURCE_V1"
TSDR_SOURCE_SEMANTICS = (
    "USPTO_TSDR_TASK_DRIVEN_SOURCE_OBSERVATIONS_NOT_CONTACT_OWNERSHIP_OR_LEGAL_CONCLUSION"
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
        "acquisition_policy_version": POLICY_VERSION,
        "acquisition_mode": "TASK_DRIVEN_EXTERNAL_COLLECTOR",
        "cadence": "WEEKLY_BATCH",
        "default_weekly_capacity": 300_000,
        "result_contract": "US_TSDR_RESULT_V1",
        "semantics": TSDR_SOURCE_SEMANTICS,
        "collector_decides_priority": False,
        "data_engine_decides_acquisition_need": True,
        "terminal_invalid_refresh_policy": "ONE_SUCCESSFUL_FINAL_OR_HISTORICAL_FETCH_THEN_RETIRE",
    }
