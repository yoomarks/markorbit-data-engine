"""Read-only IPOS metadata probe: source revision is NOT verified corpus identity.

Never downloads CSV, starts a schedule or advances an ingestion watermark.
Weekly full refresh remains due even when dataset metadata is unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .ipos_sg import IPOS_DATASET_ID

METADATA_URL = (
    f"https://api-production.data.gov.sg/v2/public/api/datasets/{IPOS_DATASET_ID}/metadata"
)
SG_TIMEZONE = timezone(timedelta(hours=8))
SG_PROBE_WEEKDAYS = frozenset({0, 2, 4})  # Mon/Wed/Fri
SG_PROBE_LOCAL_HOUR = 21
SG_WEEKLY_FULL_INTERVAL = timedelta(days=7)
MAX_METADATA_BYTES = 128 * 1024


class IposMetadataProbeError(RuntimeError):
    """Do not update the successful watermark after any probe failure."""


@dataclass(frozen=True)
class IposMetadataObservation:
    dataset_id: str
    checked_at: datetime
    source_updated_at: datetime
    dataset_size_bytes: int
    format: str
    column_metadata_sha256: str
    metadata_identity_sha256: str
    source_uri: str = METADATA_URL
    source_revision_trusted: bool = False


@dataclass(frozen=True)
class IposMaintenanceDecision:
    action: str
    full_refresh_due: bool
    metadata_changed_candidate: bool
    operator_review_required: bool
    recurring_schedule_enabled: bool = False
    downloaded_csv: bool = False


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IposMetadataProbeError(f"{label} must include timezone")
    return value.astimezone(timezone.utc)


def sg_metadata_probe_due(*, now: datetime, last_attempt_at: datetime | None) -> bool:
    """M/W/F 21:00 SGT; one attempt per slot, including failed attempts."""
    current = _aware(now, "now").astimezone(SG_TIMEZONE)
    if current.weekday() not in SG_PROBE_WEEKDAYS or current.hour < SG_PROBE_LOCAL_HOUR:
        return False
    if last_attempt_at is None:
        return True
    previous = _aware(last_attempt_at, "last_attempt_at").astimezone(SG_TIMEZONE)
    return previous.date() != current.date()


def parse_ipos_metadata(
    payload: Mapping[str, Any], *, checked_at: datetime
) -> IposMetadataObservation:
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise IposMetadataProbeError("Official metadata response is not successful")
    data = payload["data"]
    if data.get("datasetId") != IPOS_DATASET_ID or data.get("format") != "CSV":
        raise IposMetadataProbeError("Official IPOS dataset identity or format changed")
    timestamp = data.get("lastUpdatedAt")
    if not isinstance(timestamp, str):
        raise IposMetadataProbeError("Official metadata has no lastUpdatedAt")
    try:
        updated = _aware(datetime.fromisoformat(timestamp.replace("Z", "+00:00")), "lastUpdatedAt")
    except (ValueError, IposMetadataProbeError) as exc:
        raise IposMetadataProbeError("Official metadata timestamp is invalid") from exc
    size = data.get("datasetSize")
    if type(size) is not int or size < 1:
        raise IposMetadataProbeError("Official metadata size is invalid")
    columns = data.get("columnMetadata")
    if not isinstance(columns, (dict, list)) or not columns:
        raise IposMetadataProbeError("Official metadata column schema is absent")
    columns_digest = hashlib.sha256(
        json.dumps(columns, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    identity = {
        "datasetId": IPOS_DATASET_ID,
        "format": "CSV",
        "lastUpdatedAt": updated.isoformat(),
        "datasetSize": size,
        "columnMetadataSha256": columns_digest,
    }
    return IposMetadataObservation(
        dataset_id=IPOS_DATASET_ID,
        checked_at=_aware(checked_at, "checked_at"),
        source_updated_at=updated,
        dataset_size_bytes=size,
        format="CSV",
        column_metadata_sha256=columns_digest,
        metadata_identity_sha256=hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )


def probe_ipos_metadata(
    *,
    api_key: str | None = None,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    max_attempts: int = 2,
) -> IposMetadataObservation:
    """GET only the official metadata; never initiate a CSV download."""
    if not 1 <= max_attempts <= 3:
        raise ValueError("max_attempts must be 1..3")
    headers = {
        "Accept": "application/json",
        "User-Agent": "markorbit-data-engine/ipos-metadata-probe",
    }
    if api_key:
        headers["x-api-key"] = api_key
    for attempt in range(max_attempts):
        try:
            with opener(
                Request(METADATA_URL, headers=headers, method="GET"), timeout=12
            ) as response:
                if response.geturl() != METADATA_URL:
                    raise IposMetadataProbeError("Official metadata endpoint redirected")
                raw = response.read(MAX_METADATA_BYTES + 1)
                if len(raw) > MAX_METADATA_BYTES:
                    raise IposMetadataProbeError("Official metadata exceeds byte budget")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise IposMetadataProbeError("Official metadata is not a JSON object")
            return parse_ipos_metadata(value, checked_at=now())
        except HTTPError as exc:
            if exc.code != 429 and exc.code < 500:
                raise IposMetadataProbeError(f"Official metadata returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt + 1 == max_attempts:
                raise IposMetadataProbeError(
                    "Official metadata transport exhausted retries"
                ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise IposMetadataProbeError("Official metadata has invalid JSON or UTF-8") from exc
        if attempt + 1 < max_attempts:
            sleeper(float(2**attempt))
    raise IposMetadataProbeError("Official metadata exhausted bounded HTTP retries")


def decide_ipos_maintenance(
    *,
    observation: IposMetadataObservation,
    prior_observation: IposMetadataObservation | None,
    last_full_success_at: datetime | None,
    now: datetime,
) -> IposMaintenanceDecision:
    """Metadata equality cannot skip a due weekly full snapshot."""
    current = _aware(now, "now")
    if observation.checked_at > current + timedelta(minutes=5):
        raise IposMetadataProbeError("Observed metadata is in the future")
    if prior_observation and prior_observation.dataset_id != observation.dataset_id:
        raise IposMetadataProbeError("Metadata observation source changed")
    if prior_observation and observation.source_updated_at < prior_observation.source_updated_at:
        return IposMaintenanceDecision("SOURCE_METADATA_REGRESSION_REVIEW", False, True, True)
    changed = prior_observation is None or (
        observation.metadata_identity_sha256 != prior_observation.metadata_identity_sha256
    )
    if last_full_success_at is not None and _aware(
        last_full_success_at, "last_full_success_at"
    ) > current + timedelta(minutes=5):
        raise IposMetadataProbeError("Accepted full snapshot time is in the future")
    full_due = last_full_success_at is None or (
        current - _aware(last_full_success_at, "last_full_success_at") >= SG_WEEKLY_FULL_INTERVAL
    )
    if full_due:
        return IposMaintenanceDecision("WEEKLY_FULL_OPERATOR_REVIEW", True, changed, True)
    if changed:
        return IposMaintenanceDecision("METADATA_CHANGE_CANDIDATE_REVIEW", False, True, True)
    return IposMaintenanceDecision("PROBE_ONLY_WEEKLY_FULL_NOT_DUE", False, False, False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only IPOS metadata probe, no CSV download")
    parser.add_argument(
        "--check", action="store_true", help="one official GET and secret-free observation"
    )
    arguments = parser.parse_args()
    if not arguments.check:
        parser.error("explicit --check is required; no schedule is enabled")
    observation = probe_ipos_metadata(api_key=os.getenv("DATA_GOV_SG_API_KEY"))
    payload = asdict(observation)
    payload["checked_at"] = observation.checked_at.isoformat()
    payload["source_updated_at"] = observation.source_updated_at.isoformat()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
