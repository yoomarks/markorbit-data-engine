from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


INTENT_CONTRACT_VERSION = "US_TSDR_SPARSE_ACQUISITION_INTENT_V1"


class TsdrResourceType(StrEnum):
    STATUS_CONTACT = "STATUS_CONTACT"
    DOCUMENT = "DOCUMENT"
    LOGO = "LOGO"


class TsdrIntentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TsdrAcquisitionIntent:
    serial_number: str
    resource_type: TsdrResourceType
    priority: int
    reason_codes: tuple[str, ...]
    requested_by: str
    force_refresh: bool = False

    def __post_init__(self) -> None:
        serial = self.serial_number.strip()
        if len(serial) != 8 or not serial.isdigit():
            raise TsdrIntentError("serial_number must contain exactly 8 digits")
        if type(self.priority) is not int or not 0 <= self.priority <= 1_000_000:
            raise TsdrIntentError("priority must be an integer from 0 to 1000000")
        requester = self.requested_by.strip()
        if not requester or len(requester) > 128:
            raise TsdrIntentError("requested_by is required and must be <= 128 characters")
        if not self.reason_codes:
            raise TsdrIntentError("at least one reason_code is required")
        normalized: list[str] = []
        for reason in self.reason_codes:
            value = str(reason).strip().upper()
            if not value or len(value) > 96:
                raise TsdrIntentError(
                    "reason_codes must contain non-empty values <= 96 characters"
                )
            normalized.append(value)
        if len(set(normalized)) != len(normalized):
            raise TsdrIntentError("reason_codes must not contain duplicates")

    @property
    def normalized_serial_number(self) -> str:
        return self.serial_number.strip()

    @property
    def normalized_requested_by(self) -> str:
        return self.requested_by.strip()

    @property
    def normalized_reason_codes(self) -> tuple[str, ...]:
        return tuple(str(value).strip().upper() for value in self.reason_codes)


def dedupe_intents(
    intents: Iterable[TsdrAcquisitionIntent],
) -> list[TsdrAcquisitionIntent]:
    """Keep the highest-priority explicit intent per serial/resource pair.

    This is scheduling mechanics only. It does not discover opportunities,
    infer commercial value, inspect applicant nationality, or create
    acquisition need.
    """
    selected: dict[
        tuple[str, TsdrResourceType], TsdrAcquisitionIntent
    ] = {}
    for item in intents:
        key = (item.normalized_serial_number, item.resource_type)
        current = selected.get(key)
        if current is None or (item.priority, item.force_refresh) > (
            current.priority,
            current.force_refresh,
        ):
            selected[key] = item

    return sorted(
        selected.values(),
        key=lambda item: (
            -item.priority,
            item.resource_type.value,
            item.normalized_serial_number,
        ),
    )


def contract_descriptor() -> dict[str, object]:
    return {
        "version": INTENT_CONTRACT_VERSION,
        "decision_owner": "UPSTREAM_CAPABILITY_OR_PRODUCT",
        "data_engine_role": "FACTS_AND_BOUNDED_ACQUISITION_EXECUTION_ONLY",
        "resource_types": [item.value for item in TsdrResourceType],
        "opportunity_discovery_permitted": False,
        "commercial_scoring_permitted": False,
        "applicant_segment_inference_permitted": False,
        "planner_semantics": [
            "DEDUPE",
            "COOLDOWN",
            "BUDGET",
            "RATE_LIMIT",
            "RETRY",
        ],
    }
