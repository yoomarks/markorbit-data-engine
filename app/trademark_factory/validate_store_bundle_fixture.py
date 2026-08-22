from __future__ import annotations

import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.migrations import assert_global_trademark_schema
from app.global_trademarks.source_objects import register_source_object
from app.trademark_factory.mapping import MappingContract, MappingRule, SelectorKind
from app.trademark_factory.store_bundle import (
    NATIVE_STORE_BUNDLE_VERSION,
    NativeStoreBundle,
    StoreBinding,
    append_native_record_bundle,
    install_native_store_bundle,
)
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import NativeColumn, NativeSqlType, ObservationTableSpec


_SCHEMA = "trademark_store_bundle_fixture"
_SOURCE_ID = "XX_STORE_BUNDLE_FIXTURE"


def _contract(domain: ObservationDomain, rules: tuple[tuple[str, str, bool], ...]) -> MappingContract:
    return MappingContract(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        version="XX_MAPPING_V1",
        rules=tuple(
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector=selector,
                domain=domain,
                target_field=target,
                required=required,
            )
            for selector, target, required in rules
        ),
    )


def _bundle() -> NativeStoreBundle:
    return NativeStoreBundle(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        store_schema=_SCHEMA,
        bindings=(
            StoreBinding(
                binding_id="record",
                spec=ObservationTableSpec(
                    schema_name=_SCHEMA,
                    table_name="record_observation",
                    domain=ObservationDomain.RECORD,
                    native_columns=(
                        NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                        NativeColumn("mark_text", NativeSqlType.TEXT),
                    ),
                ),
                contract=_contract(
                    ObservationDomain.RECORD,
                    (
                        ("/application/number", "application_number", True),
                        ("/mark/text", "mark_text", False),
                    ),
                ),
            ),
            StoreBinding(
                binding_id="party",
                spec=ObservationTableSpec(
                    schema_name=_SCHEMA,
                    table_name="party_observation",
                    domain=ObservationDomain.PARTY,
                    native_columns=(
                        NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                        NativeColumn("party_name", NativeSqlType.TEXT, nullable=False),
                        NativeColumn("party_country", NativeSqlType.TEXT),
                    ),
                ),
                contract=_contract(
                    ObservationDomain.PARTY,
                    (
                        ("/application/number", "application_number", True),
                        ("/owner/name", "party_name", True),
                        ("/owner/country", "party_country", False),
                    ),
                ),
            ),
            StoreBinding(
                binding_id="goods",
                spec=ObservationTableSpec(
                    schema_name=_SCHEMA,
                    table_name="goods_observation",
                    domain=ObservationDomain.GOODS_SERVICE,
                    native_columns=(
                        NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                        NativeColumn("class_number", NativeSqlType.TEXT, nullable=False),
                        NativeColumn("goods_text", NativeSqlType.TEXT, nullable=False),
                    ),
                ),
                contract=_contract(
                    ObservationDomain.GOODS_SERVICE,
                    (
                        ("/application/number", "application_number", True),
                        ("/goods/class", "class_number", True),
                        ("/goods/text", "goods_text", True),
                    ),
                ),
            ),
        ),
    )


def main() -> int:
    assert_global_trademark_schema()
    bundle = _bundle()
    assert bundle.validate() == (), bundle.validate()

    with tempfile.TemporaryDirectory(prefix="native-store-bundle-") as temporary:
        source_path = Path(temporary) / "xx-records.json"
        source_path.write_text('{"fixture":"store-bundle"}\n', encoding="utf-8")
        source_object_id = register_source_object(
            jurisdiction="XX",
            source_id=_SOURCE_ID,
            path=source_path,
            metadata={"fixture": "store-bundle"},
        )

        native = {
            "application": {"number": "1001"},
            "mark": {"text": "ORBIT TEST"},
            "owner": {"name": "Example Owner", "country": "CA"},
            "goods": {"class": "9", "text": "downloadable software"},
        }

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
                install_native_store_bundle(cur, bundle)

                first = append_native_record_bundle(
                    cur,
                    bundle,
                    native=native,
                    record_key="XX-1001",
                    source_object_id=source_object_id,
                    source_index=1,
                    parser_version="XX_PARSER_V1",
                )
                assert first.inserted_count == 3
                assert first.replay_count == 0
                assert first.inserted_by_binding == {
                    "record": True,
                    "party": True,
                    "goods": True,
                }

                replay = append_native_record_bundle(
                    cur,
                    bundle,
                    native=native,
                    record_key="XX-1001",
                    source_object_id=source_object_id,
                    source_index=1,
                    parser_version="XX_PARSER_V1",
                )
                assert replay.inserted_count == 0
                assert replay.replay_count == 3

                cur.execute(
                    f"SELECT application_number, mark_text, source_payload "
                    f"FROM {_SCHEMA}.record_observation"
                )
                record = cur.fetchone()
                assert record["application_number"] == "1001"
                assert record["mark_text"] == "ORBIT TEST"
                assert record["source_payload"] == native

                cur.execute(
                    f"SELECT party_name, party_country FROM {_SCHEMA}.party_observation"
                )
                party = cur.fetchone()
                assert party == {"party_name": "Example Owner", "party_country": "CA"}

                cur.execute(
                    f"SELECT class_number, goods_text FROM {_SCHEMA}.goods_observation"
                )
                goods = cur.fetchone()
                assert goods == {"class_number": "9", "goods_text": "downloadable software"}

                drift_blocked = False
                try:
                    append_native_record_bundle(
                        cur,
                        bundle,
                        native={**native, "mark": {"text": "DIFFERENT"}},
                        record_key="XX-1001",
                        source_object_id=source_object_id,
                        source_index=1,
                        parser_version="XX_PARSER_V1",
                    )
                except RuntimeError as exc:
                    drift_blocked = "nondeterministic native observation replay" in str(exc)
                assert drift_blocked

                cur.execute(f"DROP SCHEMA {_SCHEMA} CASCADE")
            conn.commit()

    print(
        {
            "status": "PASS",
            "native_store_bundle_version": NATIVE_STORE_BUNDLE_VERSION,
            "multi_domain_native_store": True,
            "explicit_schema_install": True,
            "single_record_atomic_cursor_boundary": True,
            "identical_bundle_replay_idempotent": True,
            "same_lineage_drift_blocked": True,
            "country_native_fields_preserved": True,
            "global_common_table_created": False,
            "current_projection_implemented": False,
            "legal_conclusion": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
