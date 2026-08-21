from __future__ import annotations

from app.global_trademarks.diagnostics import collect_readiness_audit


def main() -> int:
    audit = collect_readiness_audit()
    payload = audit.as_dict()

    assert audit.schema_ready is True
    assert audit.acquisition_schema_ready is True
    assert audit.missing_acquisition_relations == ()

    jurisdictions = {item.jurisdiction: item for item in audit.jurisdictions}
    assert set(jurisdictions) == {"US", "GB", "EU", "CA", "AU", "NZ"}

    for jurisdiction in ("GB", "EU", "CA", "AU", "NZ"):
        item = jurisdictions[jurisdiction]
        assert item.schema_ready is True
        assert item.missing_relations == ()
        assert item.configured_sources >= 2
        assert item.active_sources >= 1
        assert item.pipeline_ready_sources >= 1

    # The fixture suite executes resumable country ingestion before this audit.
    # A leftover RUNNING state would indicate that interruption/recovery semantics
    # failed to close an ingest run cleanly.
    assert all(item.running_runs == 0 for item in audit.jurisdictions)

    print({"status": "PASS", "read_only": True, "audit": payload})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
