import io
import json
from urllib.request import Request

from app.snapshot_delta.acquisition import DataGovSgSnapshotDownloader


class FakeResponse:
    def __init__(self, payload: dict):
        self._stream = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def test_download_api_requests_send_explicit_user_agent():
    requests: list[Request] = []
    responses = iter(
        [
            FakeResponse({"code": 0, "data": {}}),
            FakeResponse({"code": 0, "data": {"url": "https://download.example/ipos.csv"}}),
        ]
    )

    def opener(request: Request, **kwargs):
        requests.append(request)
        return next(responses)

    downloader = DataGovSgSnapshotDownloader(opener=opener, api_key="secret-key")

    assert downloader.resolve_download_url() == "https://download.example/ipos.csv"
    assert len(requests) == 2
    for request in requests:
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers["user-agent"] == "markorbit-data-engine/ipos-snapshot-acquisition"
        assert headers["x-api-key"] == "secret-key"
