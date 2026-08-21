from __future__ import annotations

import csv
import tempfile
from datetime import date
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.acceptance import (
    evaluate_manifest_acceptance,
    evaluate_manifest_data_trust,
)
from app.global_trademarks.gb_open_data import UK_FIELDS, ingest_ukipo_2018
from app.global_trademarks.ingest_runs import (
    begin_or_resume_ingest_run,
    complete_ingest_run,
)
from app.global_trademarks.manifest import attach_manifest_object, upsert_source_manifest
from app.global_trademarks.migrations import migrate_global_trademark_schema
from app.global_trademarks.source_objects import register_source_object


def _uk_fixture(path: Path) -> None:
    fields = [*UK_FIELDS, *[f"Class{number}" for number in range(1, 46)]]
    row = {field: "" for field in fields}
    row.update(
        {
            "Trade Mark": "UK00000008888",
            "Mark Text": "ACCEPTANCE CONTRACT",
            "Name": "Acceptance Owner Limited",
            "Status": "Registered",
            "Filed": "2018-01-01",
            "Registered": "2018-06-01",
            "Renewal Due Date": "2028-06-01",
            "Class9": "1",
        }
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="|")
        writer.writeheader()
        writer.writerow(row)


def _complete_wrong_pipeline(source_object_id) -> None:
    wrong = begin_or_resume_ingest_run(
        source_object_id=source_object_id,
        jurisdiction="GB",
        pipeline_id="UKIPO_2018_MADRID_IR_V1",
        metadata={"fixture": "wrong pipeline must not satisfy acceptance"},
    )
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            complete_ingest_run(
                cur,
                run_id=wrong.run_id,
                checkpoint=0,
                rows_committed=0,
            )
        conn.commit()


def main() -> int:
    assert migrate_global_trademark_schema().ready

    with tempfile.TemporaryDirectory(prefix="global-acceptance-fixture-") as temporary:
        root = Path(temporary)
        source_path = root / "OpenDataDomestic2018.txt"
        _uk_fixture(source_path)
        source_object_id = register_source_object(
            jurisdiction="GB",
            source_id="UKIPO_OPEN_DATA_2018",
            path=source_path,
            source_period_start=date(2018, 1, 1),
            source_period_end=date(2018, 12, 31),
            metadata={"intended_pipeline_id": "UKIPO_2018_DOMESTIC_V1"},
        )
        manifest = upsert_source_manifest(
            jurisdiction="GB",
            source_id="UKIPO_OPEN_DATA_2018",
            manifest_key="UKIPO_2018_ACCEPTANCE_FIXTURE",
            source_period_start=date(2018, 1, 1),
            source_period_end=date(2018, 12, 31),
            source_sequence=1,
            source_precedence=10,
            expected_objects=1,
            parser_version="UKIPO_2018_V1",
            mapping_version="COUNTRY_NATIVE_V1",
        )
        manifest = attach_manifest_object(
            manifest_id=manifest.manifest_id,
            source_object_id=source_object_id,
            part_sequence=1,
        )

        before = evaluate_manifest_acceptance(manifest.manifest_id)
        assert before.release_accepted is False
        assert before.intended_pipeline_identity_complete is True
        assert "SOURCE_OBJECT_WITHOUT_COMPLETE_INGEST_RUN" in before.reason_codes

        _complete_wrong_pipeline(source_object_id)
        wrong_pipeline = evaluate_manifest_acceptance(manifest.manifest_id)
        assert wrong_pipeline.release_accepted is False
        assert wrong_pipeline.complete_run_objects == 0
        assert "SOURCE_OBJECT_WITHOUT_COMPLETE_INGEST_RUN" in wrong_pipeline.reason_codes

        assert ingest_ukipo_2018(source_path, source_stream="DOMESTIC") == 1
        accepted = evaluate_manifest_acceptance(manifest.manifest_id)
        assert accepted.release_accepted is True
        assert accepted.authoritative_source is True
        assert accepted.pipeline_ready is True
        assert accepted.objects_complete is True
        assert accepted.part_sequence_complete is True
        assert accepted.source_identity_complete is True
        assert accepted.intended_pipeline_identity_complete is True
        assert accepted.sha_verified is True
        assert accepted.complete_run_objects == 1
        assert accepted.missing_run_objects == 0
        assert accepted.reason_codes == ()
        assert accepted.as_dict()["jurisdiction_current_state_accepted"] is False
        assert accepted.as_dict()["legal_conclusion"] is False

        acceptance, trust = evaluate_manifest_data_trust(
            manifest.manifest_id,
            required_coverage_through=date(2018, 12, 31),
        )
        assert acceptance.release_accepted is True
        assert trust.queryable is True
        assert trust.complete is True
        assert trust.fresh is True
        assert trust.accepted is True
        assert trust.trusted_for_silence is False

        incomplete = upsert_source_manifest(
            jurisdiction="GB",
            source_id="UKIPO_OPEN_DATA_2018",
            manifest_key="UKIPO_2018_INCOMPLETE_FIXTURE",
            source_sequence=2,
            source_precedence=10,
            expected_objects=2,
            predecessor_manifest_key=manifest.manifest_key,
            parser_version="UKIPO_2018_V1",
            mapping_version="COUNTRY_NATIVE_V1",
        )
        incomplete = attach_manifest_object(
            manifest_id=incomplete.manifest_id,
            source_object_id=source_object_id,
            part_sequence=2,
        )
        incomplete_result = evaluate_manifest_acceptance(incomplete.manifest_id)
        assert incomplete_result.release_accepted is False
        assert "MANIFEST_OBJECT_SET_INCOMPLETE" in incomplete_result.reason_codes
        assert "MANIFEST_PART_SEQUENCE_INCOMPLETE" in incomplete_result.reason_codes
        assert incomplete_result.predecessor_resolved is True

        au_path = root / "au-source.csv"
        au_path.write_text("ip_right_type,application_number,status\n", encoding="utf-8")
        au_object_id = register_source_object(
            jurisdiction="AU",
            source_id="IPGOD_2022",
            path=au_path,
        )
        identity_blocked = False
        try:
            attach_manifest_object(
                manifest_id=manifest.manifest_id,
                source_object_id=au_object_id,
                part_sequence=1,
            )
        except ValueError as exc:
            identity_blocked = "does not match" in str(exc)
        assert identity_blocked is True

    print(
        {
            "status": "PASS",
            "release_acceptance_fails_closed": True,
            "exact_intended_pipeline_required": True,
            "wrong_pipeline_does_not_satisfy_acceptance": True,
            "complete_ingest_required": True,
            "part_sequence_required": True,
            "source_identity_guarded": True,
            "data_trust_projected": True,
            "trusted_for_silence_default": False,
            "jurisdiction_current_state_not_claimed": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
