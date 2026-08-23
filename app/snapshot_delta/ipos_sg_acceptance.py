"""Live-source contract acceptance for Singapore IPOS data.gov.sg activation."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .acquisition import DataGovSgSnapshotDownloader
from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS, SnapshotSource


class IposSourceAcceptanceError(RuntimeError):
    """Raised when the live IPOS source no longer satisfies the source contract."""


@dataclass(frozen=True)
class IposSourceAcceptance:
    dataset_id: str
    checked_at: datetime
    total_rows: int
    field_names: tuple[str, ...]
    sample_application_number: str
    sample_mark_status: str
    download_url_resolved: bool = False


def _field_name(field: Any) -> str:
    if not isinstance(field, dict):
        return ""
    value = field.get("id") or field.get("name")
    return str(value).strip() if value is not None else ""


def _request_json(
    url: str,
    *,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    timeout_seconds: float = 30.0,
    api_key: str | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    headers = {"Accept": "application/json", "User-Agent": "markorbit-data-engine/ipos-acceptance"}
    if api_key:
        headers["x-api-key"] = api_key

    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            with opener(Request(url, headers=headers), timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise IposSourceAcceptanceError("data.gov.sg returned a non-object JSON response")
            return payload
        except HTTPError as exc:
            last_error = exc
            if exc.code != 429 and exc.code < 500:
                raise IposSourceAcceptanceError(
                    f"data.gov.sg source probe failed with HTTP {exc.code}"
                ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc

        if attempt + 1 < max_attempts:
            sleeper(float(2**attempt))

    raise IposSourceAcceptanceError("data.gov.sg source probe exhausted retries") from last_error


def probe_ipos_live_source(
    source: SnapshotSource = IPOS_SG_TRADEMARK_APPLICATIONS,
    *,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    timeout_seconds: float = 30.0,
    api_key: str | None = None,
    resolve_download_url: bool = False,
) -> IposSourceAcceptance:
    """Probe one live row and critical schema without downloading the multi-GB snapshot."""
    query_url = f"{source.api_url}&{urlencode({'limit': 1})}"
    payload = _request_json(
        query_url,
        opener=opener,
        sleeper=sleeper,
        timeout_seconds=timeout_seconds,
        api_key=api_key,
    )

    if payload.get("success") is not True:
        raise IposSourceAcceptanceError("data.gov.sg datastore_search did not report success")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise IposSourceAcceptanceError("data.gov.sg response is missing result")

    fields = result.get("fields")
    if not isinstance(fields, list):
        raise IposSourceAcceptanceError("data.gov.sg response is missing fields")
    field_names = tuple(name for field in fields if (name := _field_name(field)))
    required = {"applicationNumber", "markStatus"}
    missing = sorted(required.difference(field_names))
    if missing:
        raise IposSourceAcceptanceError(
            f"IPOS live schema missing required fields: {', '.join(missing)}"
        )

    records = result.get("records")
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        raise IposSourceAcceptanceError("IPOS live source returned no sample record")
    sample = records[0]
    application_number = str(sample.get("applicationNumber") or "").strip()
    if not application_number:
        raise IposSourceAcceptanceError("IPOS live sample has no applicationNumber")
    if "markStatus" not in sample:
        raise IposSourceAcceptanceError("IPOS live sample has no markStatus")

    total_raw = result.get("total")
    try:
        total_rows = int(total_raw)
    except (TypeError, ValueError) as exc:
        raise IposSourceAcceptanceError("IPOS live source returned an invalid total row count") from exc
    if total_rows < 1:
        raise IposSourceAcceptanceError("IPOS live source reported an empty dataset")

    download_resolved = False
    if resolve_download_url:
        downloader = DataGovSgSnapshotDownloader(
            source,
            opener=opener,
            sleeper=sleeper,
            timeout_seconds=timeout_seconds,
        )
        download_resolved = bool(downloader.resolve_download_url())

    return IposSourceAcceptance(
        dataset_id=source.dataset_id,
        checked_at=datetime.now(timezone.utc),
        total_rows=total_rows,
        field_names=field_names,
        sample_application_number=application_number,
        sample_mark_status=str(sample.get("markStatus") or ""),
        download_url_resolved=download_resolved,
    )


def _json_payload(result: IposSourceAcceptance) -> dict[str, Any]:
    payload = asdict(result)
    payload["checked_at"] = result.checked_at.isoformat()
    payload["field_names"] = list(result.field_names)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe the live Singapore IPOS source contract")
    parser.add_argument(
        "--resolve-download-url",
        action="store_true",
        help="also exercise data.gov.sg initiate/poll download URL resolution without downloading CSV",
    )
    args = parser.parse_args()
    result = probe_ipos_live_source(
        api_key=os.getenv("DATA_GOV_SG_API_KEY"),
        resolve_download_url=args.resolve_download_url,
    )
    print(json.dumps(_json_payload(result), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
