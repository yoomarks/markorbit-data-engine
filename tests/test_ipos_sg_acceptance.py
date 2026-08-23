import json

import pytest

from app.snapshot_delta.ipos_sg_acceptance import (
    IposSourceAcceptanceError,
    probe_ipos_live_source,
)
from app.snapshot_delta.ipos_sg_native_facts import IPOS_NATIVE_SOURCE_FIELDS


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
            if fields is not None
            else [{"id": field} for field in (*IPOS_NATIVE_SOURCE_FIELDS, "_id")],
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


def test_probe_accepts_complete_live_datastore_contract():
    seen = []

    def opener(request, *, timeout):
        seen.append((request.full_url, request.headers, timeout))
        return _Response(_payload())

    result = probe_ipos_live_source(opener=opener, sleeper=lambda _: None)

    assert result.dataset_id == "d_6145acb2130bf781165258e76a584383"
    assert result.total_rows == 874900
    assert result.sample_application_number == "40202600001A"
    assert result.sample_mark_status == "Registered"
    assert set(IPOS_NATIVE_SOURCE_FIELDS).issubset(result.field_names)
    assert "_id" in result.field_names
    assert seen[0][0].endswith("&limit=1")


def test_probe_rejects_critical_schema_drift_before_full_contract_check():
    fields = [
        {"id": field}
        for field in IPOS_NATIVE_SOURCE_FIELDS
        if field != "markStatus"
    ]

    def opener(request, *, timeout):
        return _Response(_payload(fields=fields))

    with pytest.raises(IposSourceAcceptanceError, match="markStatus"):
        probe_ipos_live_source(opener=opener, sleeper=lambda _: None)


def test_probe_rejects_missing_noncritical_native_source_field():
    fields = [
        {"id": field}
        for field in IPOS_NATIVE_SOURCE_FIELDS
        if field != "agentCorrespondenceDetails_json"
    ]

    def opener(request, *, timeout):
        return _Response(_payload(fields=fields))

    with pytest.raises(
        IposSourceAcceptanceError,
        match="missing=agentCorrespondenceDetails_json",
    ):
        probe_ipos_live_source(opener=opener, sleeper=lambda _: None)


def test_probe_rejects_new_unknown_native_source_field():
    fields = [
        *({"id": field} for field in IPOS_NATIVE_SOURCE_FIELDS),
        {"id": "futureSourceEvidence"},
    ]

    def opener(request, *, timeout):
        return _Response(_payload(fields=fields))

    with pytest.raises(IposSourceAcceptanceError, match="unknown=futureSourceEvidence"):
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
