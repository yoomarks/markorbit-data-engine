from __future__ import annotations

import base64
import json

import pytest

from app.discovery_contract import (
    ABSOLUTE_MAX_PAGE_SIZE,
    DiscoveryContractError,
    DiscoveryCursorError,
    DiscoveryLimits,
    build_page_provenance,
    build_query_identity,
    build_snapshot_ref,
    decode_cursor,
    encode_cursor,
    verify_query_identity,
)


def _limits() -> DiscoveryLimits:
    return DiscoveryLimits(page_size=50, max_pages=10, max_results=300)


def _query(*, status: str = "PENDING") -> dict:
    return build_query_identity(
        stream_id="phase4-example",
        source_schema_id="EXAMPLE_CURRENT_V1",
        candidate_type="example_candidate",
        projection_fields=["candidate_id", "filing_date"],
        scope={"jurisdiction": "CN", "status": status},
        limits=_limits(),
    )


def _snapshot() -> dict:
    return build_snapshot_ref(
        snapshot_id="cn-serving-epoch-85",
        snapshot_kind="serving_watermark",
        watermark="coverage=2026-07-31:max-success-sequence=85",
        source_version="git:e57ae0358c691778c8f67e54c3adaedbe4ef8887",
    )


def test_query_identity_is_stable_across_scope_key_order():
    first = build_query_identity(
        stream_id="phase4-example",
        source_schema_id="EXAMPLE_CURRENT_V1",
        candidate_type="example_candidate",
        projection_fields=["candidate_id", "filing_date"],
        scope={"jurisdiction": "CN", "status": "PENDING"},
        limits=_limits(),
    )
    second = build_query_identity(
        stream_id="phase4-example",
        source_schema_id="EXAMPLE_CURRENT_V1",
        candidate_type="example_candidate",
        projection_fields=["candidate_id", "filing_date"],
        scope={"status": "PENDING", "jurisdiction": "CN"},
        limits=_limits(),
    )

    assert first["query_hash"] == second["query_hash"]
    assert verify_query_identity(first) == first


def test_query_identity_changes_when_scope_changes():
    assert _query(status="PENDING")["query_hash"] != _query(status="PUBLISHED")["query_hash"]


def test_query_identity_rejects_forged_hash():
    query = _query()
    query["query_hash"] = "sha256:" + "0" * 64

    with pytest.raises(DiscoveryContractError, match="hash mismatch"):
        verify_query_identity(query)


def test_limits_reject_unbounded_page_size():
    with pytest.raises(DiscoveryContractError, match="page_size exceeds"):
        DiscoveryLimits(
            page_size=ABSOLUTE_MAX_PAGE_SIZE + 1,
            max_pages=10,
            max_results=300,
        )


def test_cursor_round_trip_binds_query_snapshot_and_keyset_position():
    query = _query()
    snapshot = _snapshot()
    token = encode_cursor(
        query_hash=query["query_hash"],
        snapshot_id=snapshot["snapshot_id"],
        position=["2026-08-01", 12345],
        next_page=2,
        emitted_count=50,
        limits=_limits(),
    )

    decoded = decode_cursor(
        token,
        expected_query_hash=query["query_hash"],
        expected_snapshot_id=snapshot["snapshot_id"],
        limits=_limits(),
    )

    assert decoded["position"] == ["2026-08-01", 12345]
    assert decoded["next_page"] == 2
    assert decoded["emitted_count"] == 50


def test_cursor_fails_closed_on_query_mismatch():
    query = _query(status="PENDING")
    other_query = _query(status="PUBLISHED")
    snapshot = _snapshot()
    token = encode_cursor(
        query_hash=query["query_hash"],
        snapshot_id=snapshot["snapshot_id"],
        position=[100],
        next_page=2,
        emitted_count=50,
        limits=_limits(),
    )

    with pytest.raises(DiscoveryCursorError, match="cursor/query mismatch"):
        decode_cursor(
            token,
            expected_query_hash=other_query["query_hash"],
            expected_snapshot_id=snapshot["snapshot_id"],
            limits=_limits(),
        )


def test_cursor_fails_closed_on_snapshot_mismatch():
    query = _query()
    snapshot = _snapshot()
    token = encode_cursor(
        query_hash=query["query_hash"],
        snapshot_id=snapshot["snapshot_id"],
        position=[100],
        next_page=2,
        emitted_count=50,
        limits=_limits(),
    )

    with pytest.raises(DiscoveryCursorError, match="cursor/snapshot mismatch"):
        decode_cursor(
            token,
            expected_query_hash=query["query_hash"],
            expected_snapshot_id="different-serving-watermark",
            limits=_limits(),
        )


def test_cursor_checksum_detects_payload_tampering():
    query = _query()
    snapshot = _snapshot()
    token = encode_cursor(
        query_hash=query["query_hash"],
        snapshot_id=snapshot["snapshot_id"],
        position=[100],
        next_page=2,
        emitted_count=50,
        limits=_limits(),
    )
    encoded = token.encode("ascii")
    padding = b"=" * (-len(encoded) % 4)
    envelope = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    envelope["payload"]["position"] = [999]
    tampered = base64.urlsafe_b64encode(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")

    with pytest.raises(DiscoveryCursorError, match="checksum mismatch"):
        decode_cursor(
            tampered,
            expected_query_hash=query["query_hash"],
            expected_snapshot_id=snapshot["snapshot_id"],
            limits=_limits(),
        )


def test_cursor_refuses_continuation_at_result_bound():
    query = _query()
    snapshot = _snapshot()

    with pytest.raises(DiscoveryCursorError, match="result hard bound"):
        encode_cursor(
            query_hash=query["query_hash"],
            snapshot_id=snapshot["snapshot_id"],
            position=[100],
            next_page=7,
            emitted_count=_limits().max_results,
            limits=_limits(),
        )


def test_page_provenance_revalidates_next_cursor_and_exact_lineage():
    query = _query()
    snapshot = _snapshot()
    cursor = encode_cursor(
        query_hash=query["query_hash"],
        snapshot_id=snapshot["snapshot_id"],
        position=["2026-08-01", 12345],
        next_page=2,
        emitted_count=50,
        limits=_limits(),
    )

    provenance = build_page_provenance(
        query_identity=query,
        snapshot=snapshot,
        engine_version="M1.7",
        page_number=1,
        result_count=50,
        emitted_count=50,
        next_cursor=cursor,
    )

    assert provenance["query_hash"] == query["query_hash"]
    assert provenance["snapshot"] == snapshot
    assert provenance["source_schema_id"] == "EXAMPLE_CURRENT_V1"
    assert provenance["page_number"] == 1
    assert provenance["has_more"] is True


def test_page_provenance_rejects_cursor_that_skips_a_page():
    query = _query()
    snapshot = _snapshot()
    cursor = encode_cursor(
        query_hash=query["query_hash"],
        snapshot_id=snapshot["snapshot_id"],
        position=[100],
        next_page=3,
        emitted_count=50,
        limits=_limits(),
    )

    with pytest.raises(DiscoveryContractError, match="does not follow"):
        build_page_provenance(
            query_identity=query,
            snapshot=snapshot,
            engine_version="M1.7",
            page_number=1,
            result_count=50,
            emitted_count=50,
            next_cursor=cursor,
        )
