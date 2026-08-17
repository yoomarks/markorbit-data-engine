from __future__ import annotations

import json
import uuid

from app.contact_ingest.country_inference import (
    COUNTRY_INFERENCE_VERSION,
    build_reference_models,
    run_country_inference,
)
from app.contact_ingest.migrations import ensure_contact_schema
from app.db import postgres_conn


ENTITY_IDS = {
    "seed_au": uuid.UUID("c0164000-0000-0000-0000-000000000001"),
    "seed_au_2": uuid.UUID("c0164000-0000-0000-0000-000000000002"),
    "seed_au_3": uuid.UUID("c0164000-0000-0000-0000-000000000003"),
    "explicit_gb": uuid.UUID("c0164000-0000-0000-0000-000000000011"),
    "phone_gb": uuid.UUID("c0164000-0000-0000-0000-000000000012"),
    "domain_au": uuid.UUID("c0164000-0000-0000-0000-000000000013"),
    "conflict": uuid.UUID("c0164000-0000-0000-0000-000000000014"),
    "city_au": uuid.UUID("c0164000-0000-0000-0000-000000000015"),
    "known_ca": uuid.UUID("c0164000-0000-0000-0000-000000000016"),
}
SOURCE_ID = uuid.UUID("c0164000-0000-0000-0000-000000000100")


def _entity_key(name: str) -> str:
    return (name.encode("utf-8").hex() + "0" * 64)[:64]


def _insert_entity(cur, key: str, name: str, *, country: str | None = None, city: str = "") -> None:
    cur.execute(
        """
        INSERT INTO entity.entity(
            entity_id, entity_key, entity_type, canonical_name,
            normalized_name, normalized_address, country_code, city,
            status, resolution_method, source_primary, confidence_score
        ) VALUES (%s, %s, 'ORGANIZATION', %s, lower(%s), '', %s, NULLIF(%s, ''),
                  'CANDIDATE', 'FIXTURE', 'FIXTURE', 1.0)
        """,
        (ENTITY_IDS[key], _entity_key(name), name, name, country, city),
    )


def _insert_channel(cur, key: str, channel_type: str, value: str) -> None:
    cur.execute(
        """
        INSERT INTO contact.channel(
            entity_id, channel_type, channel_value, normalized_value
        ) VALUES (%s, %s, %s, %s)
        """,
        (ENTITY_IDS[key], channel_type, value, value.casefold()),
    )


def _insert_raw(cur, key: str, row_no: int, raw_data: dict[str, str]) -> None:
    cur.execute(
        """
        INSERT INTO contact.raw_record(
            source_id, source_member, sheet_name, source_row, source_profile,
            entity_id, entity_match_method, raw_data
        ) VALUES (%s, 'fixture.csv', 'fixture', %s, 'AGENT_CONTACT_LIST',
                  %s, 'FIXTURE', %s::jsonb)
        """,
        (SOURCE_ID, row_no, ENTITY_IDS[key], json.dumps(raw_data)),
    )


def _cleanup(cur) -> None:
    ids = list(ENTITY_IDS.values())
    cur.execute("DELETE FROM contact.entity_country_inference WHERE entity_id = ANY(%s::uuid[])", (ids,))
    cur.execute("DELETE FROM contact.channel WHERE entity_id = ANY(%s::uuid[])", (ids,))
    cur.execute("DELETE FROM contact.raw_record WHERE entity_id = ANY(%s::uuid[])", (ids,))
    cur.execute("DELETE FROM entity.entity_identifier WHERE entity_id = ANY(%s::uuid[])", (ids,))
    cur.execute("DELETE FROM entity.entity_mention WHERE entity_id = ANY(%s::uuid[])", (ids,))
    cur.execute("DELETE FROM entity.entity WHERE entity_id = ANY(%s::uuid[])", (ids,))
    cur.execute("DELETE FROM contact.country_inference_run WHERE rule_version = %s", (COUNTRY_INFERENCE_VERSION,))
    cur.execute("DELETE FROM contact.source WHERE source_id = %s", (SOURCE_ID,))


def seed_fixture() -> None:
    ensure_contact_schema()
    from app.contact_ingest.country_inference import ensure_country_inference_schema

    ensure_country_inference_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            _cleanup(cur)
            cur.execute(
                """
                INSERT INTO contact.source(
                    source_id, source_sha256, source_name, file_type, source_profile,
                    source_segment, source_scope, ingest_version
                ) VALUES (%s, %s, 'country-inference-fixture.csv', 'csv',
                          'AGENT_CONTACT_LIST', 'AGENT', 'FIXTURE', 'FIXTURE')
                """,
                (SOURCE_ID, "c0164" + "0" * 59),
            )

            _insert_entity(cur, "seed_au", "AU Seed One", country="AU", city="Sydney")
            _insert_entity(cur, "seed_au_2", "AU Seed Two", country="AU", city="Sydney")
            _insert_entity(cur, "seed_au_3", "AU Seed Three", country="AU", city="Sydney")
            _insert_channel(cur, "seed_au", "EMAIL", "known@sharedfirm.com")

            _insert_entity(cur, "explicit_gb", "Explicit GB")
            _insert_raw(cur, "explicit_gb", 11, {"Country": "UK", "Address": "London"})

            _insert_entity(cur, "phone_gb", "Phone GB")
            _insert_channel(cur, "phone_gb", "PHONE_UNKNOWN", "+44 20 8366 1177")

            _insert_entity(cur, "domain_au", "Domain AU")
            _insert_channel(cur, "domain_au", "EMAIL", "hello@sharedfirm.com")

            _insert_entity(cur, "conflict", "Conflicting Signals")
            _insert_channel(cur, "conflict", "PHONE_UNKNOWN", "+44 20 8366 1177")
            _insert_channel(cur, "conflict", "WEBSITE", "example.de")

            _insert_entity(cur, "city_au", "Sydney Contact", city="Sydney")
            _insert_channel(cur, "city_au", "WEBSITE", "sydney-contact.com.au")

            _insert_entity(cur, "known_ca", "Known Canada", country="CA", city="Toronto")
            _insert_raw(cur, "known_ca", 16, {"Country": "US"})
        conn.commit()


def _countries() -> dict[str, str | None]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entity_id::text, country_code
                FROM entity.entity
                WHERE entity_id = ANY(%s::uuid[])
                """,
                (list(ENTITY_IDS.values()),),
            )
            return {str(row["entity_id"]): row["country_code"] for row in cur.fetchall()}


def validate() -> dict[str, object]:
    seed_fixture()
    before = _countries()

    preview = run_country_inference(apply=False, batch_size=50)
    preview_countries = _countries()
    assert preview["status"] == "SUCCESS"
    assert preview["apply"] is False
    assert preview["applied"] == 0
    assert preview_countries == before

    applied = run_country_inference(apply=True, batch_size=50)
    countries = _countries()
    assert applied["status"] == "SUCCESS"
    assert countries[str(ENTITY_IDS["explicit_gb"])] == "GB"
    assert countries[str(ENTITY_IDS["phone_gb"])] == "GB"
    assert countries[str(ENTITY_IDS["domain_au"])] == "AU"
    assert countries[str(ENTITY_IDS["city_au"])] == "AU"
    assert countries[str(ENTITY_IDS["conflict"])] is None
    assert countries[str(ENTITY_IDS["known_ca"])] == "CA"

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, country_code, confidence, runner_up_country_code,
                       evidence, applied_at
                FROM contact.entity_country_inference
                WHERE entity_id = %s
                """,
                (ENTITY_IDS["conflict"],),
            )
            conflict = dict(cur.fetchone())
            assert conflict["status"] == "CONFLICT"
            assert conflict["country_code"] == "GB"
            assert conflict["runner_up_country_code"] == "DE"
            assert conflict["applied_at"] is None
            kinds = {item["kind"] for item in conflict["evidence"]}
            assert {"INTERNATIONAL_PHONE", "COUNTRY_CODE_DOMAIN"} <= kinds

            cur.execute(
                """
                SELECT status, country_code, evidence, applied_at
                FROM contact.entity_country_inference
                WHERE entity_id = %s
                """,
                (ENTITY_IDS["explicit_gb"],),
            )
            explicit = dict(cur.fetchone())
            assert explicit["status"] == "ACCEPTED"
            assert explicit["country_code"] == "GB"
            assert explicit["applied_at"] is not None
            assert "RAW_EXPLICIT_COUNTRY_FIELD" in {
                item["kind"] for item in explicit["evidence"]
            }

    models_after_apply = build_reference_models()
    # The inferred Domain AU row must not become a second training observation.
    assert models_after_apply.domain_country["sharedfirm.com"] == ("AU", 1.0, 1)
    # Likewise the inferred Sydney entity must not increase the 3 source-grounded seeds.
    assert models_after_apply.city_country["sydney"] == ("AU", 1.0, 3)

    return {
        "status": "PASS",
        "preview": preview,
        "applied": applied,
        "countries": countries,
        "domain_model_sharedfirm": models_after_apply.domain_country["sharedfirm.com"],
        "city_model_sydney": models_after_apply.city_country["sydney"],
    }


def main() -> int:
    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
