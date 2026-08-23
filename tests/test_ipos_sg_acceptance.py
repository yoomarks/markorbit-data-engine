import json

import pytest

from app.snapshot_delta.ipos_sg_acceptance import (
    IposSourceAcceptanceError,
    probe_ipos_live_source,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _payload(*, fields=None, records=None, total=874900):
    return {
        "success": True,
        "result": {
            "fields": fields
            or [
                {"id": "applicationNumber"},
                {"id": "markStatus"},
                {"id": "filingDate"},
            ],
            "records": records
            or [
                {
                    "applicationNumber": "40202600001A",
                    "markStatus": "Registered",
                    "filingDate": "2026-01-01",
                }
            ],
            "total": total,
        },
    }


def test_probe_accepts_live_datastore_contract():
    seen = []

    def opener(request, *, timeout):
        seen.append((request.full_url, request.headers, timeout))
        return _Response(_payload())

    result = probe_ipos_live_source(opener=opener, sleeper=lambda _: None)

    assert result.dataset_id == "d_6145acb2130bf781165258e76a584383"
    assert result.total_rows == 874900
    assert result.sample_application_number == "40202600001A"
    assert result.sample_mark_status == "Registered"
    assert "applicationNumber" in result.field_names
    assert "markStatus" in result.field_names
    assert seen[0][0].endswith("&limit=1")


def test_probe_rejects_critical_schema_drift():
    def opener(request, *, timeout):
        return _Response(_payload(fields=[{"id": "applicationNumber"}]))

    with pytest.raises(IposSourceAcceptanceError, match="markStatus"):
        probe_ipos_live_source(opener=opener, sleeper=lambda _: None)


def test_probe_rejects_empty_dataset():
    def opener(request, *, timeout):
        return _Response(_payload(total=0))

    with pytest.raises(IposSourceAcceptanceError, match="empty dataset"):
        probe_ipos_live_source(opener=opener, sleeper=lambda _: None)


def test_probe_rejects_sample_without_application_number():
    def opener(request, *, timeout):
        return _Response(_payload(records=[{"applicationNumber": "", "markStatus": "Pending"}]))

    with pytest.raises(IposSourceAcceptanceError, match="applicationNumber"):
        probe_ipos_live_source(opener=opener, sleeper=lambda _: None)
