import csv
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from app.snapshot_delta.acquisition import (
    DataGovSgSnapshotDownloader,
    SnapshotDownloadError,
)
from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from app.snapshot_delta.ipos_sg_schema_contract import IPOS_NATIVE_CSV_SOURCE_FIELDS


class FakeResponse:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None):
        self._stream = io.BytesIO(payload)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def json_response(payload: dict) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def valid_snapshot_bytes(
    application_number: str = "SG1", mark_status: str = "Pending"
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=IPOS_NATIVE_CSV_SOURCE_FIELDS)
    writer.writeheader()
    writer.writerow(
        {
            "Application Number": application_number,
            "Mark Status": mark_status,
        }
    )
    return stream.getvalue().encode("utf-8")


def test_anonymous_resolve_download_url_polls_until_ready():
    calls: list[str] = []
    sleeps: list[float] = []
    responses = iter(
        [
            json_response({"code": 0, "data": {}}),
            json_response({"code": 1, "errMsg": "Preparing download"}),
            json_response({"code": 0, "data": {"url": "https://download.example/ipos.csv"}}),
        ]
    )

    def opener(request: Request, **kwargs):
        calls.append(request.full_url)
        return next(responses)

    downloader = DataGovSgSnapshotDownloader(
        opener=opener,
        sleeper=sleeps.append,
        poll_interval_seconds=2.5,
        max_poll_attempts=3,
    )

    assert downloader.resolve_download_url() == "https://download.example/ipos.csv"
    assert calls == [
        IPOS_SG_TRADEMARK_APPLICATIONS.initiate_download_url,
        IPOS_SG_TRADEMARK_APPLICATIONS.poll_download_url,
        IPOS_SG_TRADEMARK_APPLICATIONS.poll_download_url,
    ]
    assert sleeps == [2.5]


def test_control_plane_request_retries_transient_network_failure():
    sleeps: list[float] = []
    calls = 0

    def opener(request: Request, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise URLError("temporary provider network failure")
        if calls == 2:
            return json_response({"code": 0, "data": {}})
        return json_response(
            {"code": 0, "data": {"url": "https://download.example/ipos.csv"}}
        )

    downloader = DataGovSgSnapshotDownloader(
        opener=opener,
        sleeper=sleeps.append,
        api_request_attempts=3,
        api_retry_base_seconds=0.25,
        max_poll_attempts=1,
    )

    assert downloader.resolve_download_url() == "https://download.example/ipos.csv"
    assert calls == 3
    assert sleeps == [0.25]


def test_control_plane_request_does_not_retry_nontransient_http_error():
    calls = 0
    sleeps: list[float] = []

    def opener(request: Request, **kwargs):
        nonlocal calls
        calls += 1
        raise HTTPError(request.full_url, 401, "Unauthorized", None, None)

    downloader = DataGovSgSnapshotDownloader(
        opener=opener,
        sleeper=sleeps.append,
        api_request_attempts=3,
        api_retry_base_seconds=0.25,
    )

    with pytest.raises(SnapshotDownloadError, match="HTTP 401"):
        downloader.resolve_download_url()

    assert calls == 1
    assert sleeps == []


def test_initiate_provider_rejection_fails_before_polling():
    calls: list[str] = []

    def opener(request: Request, **kwargs):
        calls.append(request.full_url)
        return json_response({"code": 2, "errMsg": "API key not authorized for export"})

    downloader = DataGovSgSnapshotDownloader(
        opener=opener,
        max_poll_attempts=1,
    )

    with pytest.raises(SnapshotDownloadError, match="initiate-download rejected"):
        downloader.resolve_download_url()

    assert calls == [IPOS_SG_TRADEMARK_APPLICATIONS.initiate_download_url]


def test_download_streams_valid_snapshot_and_publishes_atomically(tmp_path: Path):
    csv_payload = valid_snapshot_bytes()
    responses = iter(
        [
            json_response({"code": 0, "data": {}}),
            json_response({"code": 0, "data": {"url": "https://download.example/ipos.csv"}}),
            FakeResponse(csv_payload),
        ]
    )

    def opener(request: Request, **kwargs):
        return next(responses)

    acquired = DataGovSgSnapshotDownloader(opener=opener, chunk_size=7).download(tmp_path)

    assert acquired.path == tmp_path / "IPOSTradeMarkApplications.csv"
    assert acquired.path.read_bytes() == csv_payload
    assert acquired.source_uri == IPOS_SG_TRADEMARK_APPLICATIONS.dataset_url
    assert acquired.bytes_written == len(csv_payload)
    assert acquired.retrieved_at.tzinfo is not None
    assert not (tmp_path / ".IPOSTradeMarkApplications.csv.part").exists()


def test_download_resumes_when_signed_export_ends_before_content_length(tmp_path: Path):
    csv_payload = valid_snapshot_bytes()
    split = max(1, len(csv_payload) // 2)
    requests: list[Request] = []
    sleeps: list[float] = []
    responses = iter(
        [
            json_response({"code": 0, "data": {}}),
            json_response({"code": 0, "data": {"url": "https://download.example/ipos.csv"}}),
            FakeResponse(
                csv_payload[:split],
                {"Content-Length": str(len(csv_payload))},
            ),
            FakeResponse(
                csv_payload[split:],
                {
                    "Content-Range": (
                        f"bytes {split}-{len(csv_payload) - 1}/{len(csv_payload)}"
                    )
                },
            ),
        ]
    )

    def opener(request: Request, **kwargs):
        requests.append(request)
        return next(responses)

    acquired = DataGovSgSnapshotDownloader(
        opener=opener,
        sleeper=sleeps.append,
        chunk_size=7,
        download_request_attempts=2,
        download_retry_base_seconds=0.25,
    ).download(tmp_path)

    assert acquired.path.read_bytes() == csv_payload
    assert acquired.bytes_written == len(csv_payload)
    assert requests[3].get_header("Range") == f"bytes={split}-"
    assert sleeps == [0.25]


def test_download_retries_transient_signed_object_failure_before_first_byte(tmp_path: Path):
    csv_payload = valid_snapshot_bytes()
    calls = 0
    sleeps: list[float] = []
    responses = iter(
        [
            json_response({"code": 0, "data": {}}),
            json_response({"code": 0, "data": {"url": "https://download.example/ipos.csv"}}),
            FakeResponse(csv_payload, {"Content-Length": str(len(csv_payload))}),
        ]
    )

    def opener(request: Request, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise URLError("temporary signed object failure")
        return next(responses)

    acquired = DataGovSgSnapshotDownloader(
        opener=opener,
        sleeper=sleeps.append,
        download_request_attempts=2,
        download_retry_base_seconds=0.5,
    ).download(tmp_path)

    assert acquired.path.read_bytes() == csv_payload
    assert acquired.bytes_written == len(csv_payload)
    assert sleeps == [0.5]


def test_api_key_is_not_forwarded_to_public_v1_export_or_signed_download(tmp_path: Path):
    csv_payload = valid_snapshot_bytes()
    responses = iter(
        [
            json_response({"code": 0, "data": {}}),
            json_response({"code": 0, "data": {"url": "https://download.example/ipos.csv"}}),
            FakeResponse(csv_payload),
        ]
    )
    requests: list[Request] = []

    def opener(request: Request, **kwargs):
        requests.append(request)
        return next(responses)

    DataGovSgSnapshotDownloader(opener=opener, api_key="secret-key").download(tmp_path)

    assert [request.full_url for request in requests[:2]] == [
        IPOS_SG_TRADEMARK_APPLICATIONS.initiate_download_url,
        IPOS_SG_TRADEMARK_APPLICATIONS.poll_download_url,
    ]
    all_headers = [
        {key.lower(): value for key, value in request.header_items()}
        for request in requests
    ]
    assert all("x-api-key" not in headers for headers in all_headers)


def test_download_rejects_schema_drift_without_replacing_existing_snapshot(tmp_path: Path):
    final_path = tmp_path / "IPOSTradeMarkApplications.csv"
    final_path.write_text("Application Number,Mark Status\nSG0,Registered\n", encoding="utf-8")
    invalid_payload = b"Application Number,Unexpected Status\nSG1,Pending\n"
    responses = iter(
        [
            json_response({"code": 0, "data": {}}),
            json_response({"code": 0, "data": {"url": "https://download.example/ipos.csv"}}),
            FakeResponse(invalid_payload),
        ]
    )

    def opener(request: Request, **kwargs):
        return next(responses)

    with pytest.raises(ValueError, match="Mark Status"):
        DataGovSgSnapshotDownloader(opener=opener).download(tmp_path)

    assert final_path.read_text(encoding="utf-8").startswith("Application Number,Mark Status")
    assert not (tmp_path / ".IPOSTradeMarkApplications.csv.part").exists()


def test_download_rejects_new_unknown_source_column_before_atomic_publish(tmp_path: Path):
    final_path = tmp_path / "IPOSTradeMarkApplications.csv"
    final_path.write_bytes(valid_snapshot_bytes("SG0", "Registered"))

    stream = io.StringIO(newline="")
    fieldnames = (*IPOS_NATIVE_CSV_SOURCE_FIELDS, "Future Source Field")
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerow(
        {
            "Application Number": "SG1",
            "Mark Status": "Pending",
            "Future Source Field": "new evidence",
        }
    )
    invalid_payload = stream.getvalue().encode("utf-8")
    responses = iter(
        [
            json_response({"code": 0, "data": {}}),
            json_response({"code": 0, "data": {"url": "https://download.example/ipos.csv"}}),
            FakeResponse(invalid_payload),
        ]
    )

    def opener(request: Request, **kwargs):
        return next(responses)

    with pytest.raises(ValueError, match="unknown=Future Source Field"):
        DataGovSgSnapshotDownloader(opener=opener).download(tmp_path)

    assert final_path.read_bytes() == valid_snapshot_bytes("SG0", "Registered")
    assert not (tmp_path / ".IPOSTradeMarkApplications.csv.part").exists()


def test_resolve_download_url_fails_after_bounded_polls():
    responses = iter(
        [
            json_response({"code": 0, "data": {}}),
            json_response({"code": 1, "errMsg": "still preparing"}),
            json_response({"code": 1, "errMsg": "still preparing"}),
        ]
    )

    def opener(request: Request, **kwargs):
        return next(responses)

    downloader = DataGovSgSnapshotDownloader(
        opener=opener,
        sleeper=lambda seconds: None,
        max_poll_attempts=2,
    )

    with pytest.raises(SnapshotDownloadError, match="still preparing"):
        downloader.resolve_download_url()
