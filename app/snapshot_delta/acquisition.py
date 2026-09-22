"""Network acquisition for snapshot-first data.gov.sg sources."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .ipos_sg import DataGovSgSnapshotSource, IPOS_SG_TRADEMARK_APPLICATIONS
from .ipos_sg_observation import validate_ipos_snapshot_schema
from .ipos_sg_schema_contract import validate_ipos_native_snapshot_schema
from .loader import SnapshotCsvLoader


class SnapshotDownloadError(RuntimeError):
    """Raised when an authoritative snapshot cannot be resolved or downloaded."""


@dataclass(frozen=True)
class AcquiredSnapshot:
    path: Path
    source_uri: str
    retrieved_at: datetime
    bytes_written: int


class DataGovSgSnapshotDownloader:
    """Resolve and atomically download one data.gov.sg CSV snapshot."""

    def __init__(
        self,
        source: DataGovSgSnapshotSource = IPOS_SG_TRADEMARK_APPLICATIONS,
        *,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 15.0,
        max_poll_attempts: int = 40,
        chunk_size: int = 1024 * 1024,
        api_key: str | None = None,
        api_request_attempts: int = 3,
        api_retry_base_seconds: float = 1.0,
        download_request_attempts: int = 4,
        download_retry_base_seconds: float = 1.0,
    ) -> None:
        if max_poll_attempts < 1:
            raise ValueError("max_poll_attempts must be positive")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if api_request_attempts < 1:
            raise ValueError("api_request_attempts must be positive")
        if api_retry_base_seconds < 0:
            raise ValueError("api_retry_base_seconds must not be negative")
        if download_request_attempts < 1:
            raise ValueError("download_request_attempts must be positive")
        if download_retry_base_seconds < 0:
            raise ValueError("download_retry_base_seconds must not be negative")
        self.source = source
        self._opener = opener
        self._sleeper = sleeper
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_attempts = max_poll_attempts
        self.chunk_size = chunk_size
        self.api_key = api_key
        self.api_request_attempts = api_request_attempts
        self.api_retry_base_seconds = api_retry_base_seconds
        self.download_request_attempts = download_request_attempts
        self.download_retry_base_seconds = download_retry_base_seconds

    def _request_json(self, url: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "markorbit-data-engine/ipos-snapshot-acquisition",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key

        last_error: Exception | None = None
        for attempt in range(self.api_request_attempts):
            request = Request(url, headers=headers)
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise SnapshotDownloadError(
                        f"unexpected JSON response from data.gov.sg API: {url}"
                    )
                return payload
            except HTTPError as exc:
                last_error = exc
                if exc.code != 429 and exc.code < 500:
                    raise SnapshotDownloadError(
                        f"data.gov.sg API request failed with HTTP {exc.code}: {url}"
                    ) from exc
            except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc

            if attempt + 1 < self.api_request_attempts:
                self._sleeper(self.api_retry_base_seconds * float(2**attempt))

        raise SnapshotDownloadError(
            f"data.gov.sg API request exhausted {self.api_request_attempts} attempts: {url}"
        ) from last_error

    @staticmethod
    def _download_url(payload: dict[str, Any]) -> str | None:
        if payload.get("code") != 0:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        url = data.get("url")
        return str(url) if url else None

    @staticmethod
    def _api_error_detail(payload: dict[str, Any]) -> str:
        return str(
            payload.get("errMsg")
            or payload.get("message")
            or payload.get("error")
            or "unknown provider error"
        )

    def resolve_download_url(self) -> str:
        """Resolve the current whole-dataset export with bounded polling.

        Authenticated operators explicitly initiate materialization before polling.
        Anonymous public acceptance uses the already-materialized poll endpoint,
        because data.gov.sg rejects anonymous initiate calls while permitting poll.
        Transient control-plane failures use bounded exponential retry, while the
        outer poll loop remains the bound for materialization readiness.
        """
        if self.api_key:
            initiated = self._request_json(self.source.initiate_download_url)
            if initiated.get("code") != 0:
                raise SnapshotDownloadError(
                    "data.gov.sg initiate-download rejected the request: "
                    + self._api_error_detail(initiated)
                )

        last_payload: dict[str, Any] | None = None
        for attempt in range(self.max_poll_attempts):
            payload = self._request_json(self.source.poll_download_url)
            last_payload = payload
            download_url = self._download_url(payload)
            if download_url:
                return download_url
            if attempt + 1 < self.max_poll_attempts:
                self._sleeper(self.poll_interval_seconds)

        detail = ""
        if last_payload:
            detail = self._api_error_detail(last_payload)
        suffix = f": {detail}" if detail else ""
        raise SnapshotDownloadError(
            f"data.gov.sg download was not ready after {self.max_poll_attempts} polls{suffix}"
        )

    @staticmethod
    def _response_header(response: Any, name: str) -> str | None:
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        value = headers.get(name)
        return str(value).strip() if value is not None else None

    @staticmethod
    def _content_range(value: str | None) -> tuple[int, int, int] | None:
        if not value:
            return None
        match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)", value.strip())
        if not match:
            return None
        return tuple(int(group) for group in match.groups())

    def download(self, destination_directory: str | Path) -> AcquiredSnapshot:
        """Stream the current snapshot to disk and publish it with an atomic rename.

        Large provider exports may terminate a connection cleanly before the declared
        Content-Length has arrived. Keep the partial file private and resume the same
        signed object with HTTP Range until its exact byte length is present.
        """
        destination = Path(destination_directory)
        destination.mkdir(parents=True, exist_ok=True)
        final_path = destination / self.source.filename
        partial_path = destination / f".{self.source.filename}.part"
        download_url = self.resolve_download_url()
        retrieved_at = datetime.now(timezone.utc)
        expected_total: int | None = None
        partial_path.unlink(missing_ok=True)

        try:
            for attempt in range(self.download_request_attempts):
                offset = partial_path.stat().st_size if partial_path.exists() else 0
                headers = {
                    "Accept": "text/csv,application/octet-stream",
                    "User-Agent": "markorbit-data-engine/ipos-snapshot-acquisition",
                }
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                # Never forward the data.gov.sg API key to signed object storage.
                request = Request(download_url, headers=headers)

                request_failed = False
                try:
                    with self._opener(request, timeout=self.timeout_seconds) as response:
                        if offset:
                            content_range = self._content_range(
                                self._response_header(response, "Content-Range")
                            )
                            if content_range is None or content_range[0] != offset:
                                raise SnapshotDownloadError(
                                    "data.gov.sg resume response did not honor the requested byte range"
                                )
                            if expected_total is not None and content_range[2] != expected_total:
                                raise SnapshotDownloadError(
                                    "data.gov.sg signed export size changed during resume"
                                )
                            expected_total = content_range[2]
                        else:
                            content_length = self._response_header(response, "Content-Length")
                            if content_length:
                                expected_total = int(content_length)

                        with partial_path.open("ab" if offset else "wb") as target:
                            while True:
                                chunk = response.read(self.chunk_size)
                                if not chunk:
                                    break
                                target.write(chunk)
                            target.flush()
                            os.fsync(target.fileno())
                except (HTTPError, URLError, TimeoutError, OSError) as exc:
                    request_failed = True
                    if attempt + 1 >= self.download_request_attempts:
                        raise SnapshotDownloadError(
                            "data.gov.sg signed export download exhausted retries"
                        ) from exc

                if request_failed:
                    self._sleeper(self.download_retry_base_seconds * float(2**attempt))
                    continue

                bytes_written = partial_path.stat().st_size if partial_path.exists() else 0
                if expected_total is None:
                    break
                if bytes_written == expected_total:
                    break
                if bytes_written > expected_total:
                    raise SnapshotDownloadError(
                        "data.gov.sg signed export exceeded its declared byte length"
                    )
                if attempt + 1 >= self.download_request_attempts:
                    raise SnapshotDownloadError(
                        "data.gov.sg signed export ended before its declared byte length: "
                        f"expected={expected_total} downloaded={bytes_written}"
                    )
                self._sleeper(self.download_retry_base_seconds * float(2**attempt))

            bytes_written = partial_path.stat().st_size if partial_path.exists() else 0
            if bytes_written == 0:
                raise SnapshotDownloadError("data.gov.sg returned an empty snapshot")
            if expected_total is not None and bytes_written != expected_total:
                raise SnapshotDownloadError(
                    "data.gov.sg signed export byte length mismatch after download"
                )

            loader = SnapshotCsvLoader(partial_path)
            validate_ipos_snapshot_schema(loader)
            validate_ipos_native_snapshot_schema(loader.fieldnames())
            os.replace(partial_path, final_path)
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise

        return AcquiredSnapshot(
            path=final_path,
            # Persist a stable official source URL in manifests, not an expiring
            # signed object-storage URL that may contain temporary credentials.
            source_uri=self.source.dataset_url,
            retrieved_at=retrieved_at,
            bytes_written=bytes_written,
        )
