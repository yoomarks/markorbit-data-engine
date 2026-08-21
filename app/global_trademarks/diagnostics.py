from __future__ import annotations

from dataclasses import asdict, dataclass

from app.db import postgres_conn
from app.global_trademarks.catalog import COUNTRY_SOURCES


_REQUIRED_RELATIONS: dict[str, tuple[str, ...]] = {
    "GB": (
        "trademark_gb.historical_record",
        "trademark_gb.weekly_observation",
        "trademark_gb.comparable_relationship",
    ),
    "EU": (
        "trademark_eu.tm_link_seed",
        "trademark_eu.api_observation",
    ),
    "CA": (
        "trademark_ca.st96_record",
        "trademark_ca.record_operation",
        "trademark_ca.record_state",
        "trademark_ca.party",
        "trademark_ca.goods_service",
        "trademark_ca.event",
        "trademark_ca.relationship",
        "trademark_ca.asset",
    ),
    "AU": (
        "trademark_au.application",
        "trademark_au.party_activity",
        "trademark_au.application_link",
        "trademark_au.application_event",
        "trademark_au.application_classification",
        "trademark_au.application_description",
    ),
    "NZ": (
        "trademark_nz.tm_link_seed",
        "trademark_nz.api_observation",
    ),
}

_ACQUISITION_RELATIONS = (
    "acquisition.global_trademark_source_object",
    "acquisition.global_trademark_record_source",
    "acquisition.global_trademark_ingest_run",
)


@dataclass(frozen=True, slots=True)
class JurisdictionReadiness:
    jurisdiction: str
    store_schema: str
    schema_ready: bool
    missing_relations: tuple[str, ...]
    configured_sources: int
    active_sources: int
    pipeline_ready_sources: int
    authoritative_ready_sources: int
    source_objects: int
    complete_runs: int
    running_runs: int
    failed_runs: int


@dataclass(frozen=True, slots=True)
class ReadinessAudit:
    acquisition_schema_ready: bool
    missing_acquisition_relations: tuple[str, ...]
    jurisdictions: tuple[JurisdictionReadiness, ...]

    @property
    def schema_ready(self) -> bool:
        return self.acquisition_schema_ready and all(item.schema_ready for item in self.jurisdictions)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_ready": self.schema_ready,
            "acquisition_schema_ready": self.acquisition_schema_ready,
            "missing_acquisition_relations": list(self.missing_acquisition_relations),
            "jurisdictions": [asdict(item) for item in self.jurisdictions],
        }


def _relation_presence(cur, relations: tuple[str, ...]) -> dict[str, bool]:
    presence: dict[str, bool] = {}
    for relation in relations:
        cur.execute("SELECT to_regclass(%s) AS relation", (relation,))
        presence[relation] = cur.fetchone()["relation"] is not None
    return presence


def _safe_count(cur, sql: str, params: tuple[object, ...]) -> int:
    cur.execute(sql, params)
    return int(cur.fetchone()["count"])


def collect_readiness_audit() -> ReadinessAudit:
    """Read-only audit of country-store schema and ingestion state.

    This function deliberately performs no migrations and no ingestion. It is safe to
    run while long-running CN jobs are active because it only reads PostgreSQL metadata
    and the global-trademark acquisition tables.
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            acquisition_presence = _relation_presence(cur, _ACQUISITION_RELATIONS)
            acquisition_ready = all(acquisition_presence.values())
            jurisdictions: list[JurisdictionReadiness] = []

            for jurisdiction, plan in COUNTRY_SOURCES.items():
                if jurisdiction == "US":
                    # US owns its established schemas outside this country-store module.
                    # Report source configuration only rather than pretending those
                    # independent schemas are part of this additive migration surface.
                    jurisdictions.append(
                        JurisdictionReadiness(
                            jurisdiction=jurisdiction,
                            store_schema=plan.store_schema,
                            schema_ready=True,
                            missing_relations=(),
                            configured_sources=len(plan.sources),
                            active_sources=sum(source.active_now for source in plan.sources),
                            pipeline_ready_sources=sum(source.pipeline_ready for source in plan.sources),
                            authoritative_ready_sources=sum(
                                source.authoritative and source.pipeline_ready for source in plan.sources
                            ),
                            source_objects=0,
                            complete_runs=0,
                            running_runs=0,
                            failed_runs=0,
                        )
                    )
                    continue

                required = _REQUIRED_RELATIONS[jurisdiction]
                presence = _relation_presence(cur, required)
                missing = tuple(relation for relation, exists in presence.items() if not exists)

                source_objects = complete_runs = running_runs = failed_runs = 0
                if acquisition_ready:
                    source_ids = tuple(source.source_id for source in plan.sources)
                    source_objects = _safe_count(
                        cur,
                        "SELECT COUNT(*) AS count FROM acquisition.global_trademark_source_object WHERE source_id = ANY(%s)",
                        (list(source_ids),),
                    )
                    cur.execute(
                        """
                        SELECT status, COUNT(*) AS count
                        FROM acquisition.global_trademark_ingest_run
                        WHERE jurisdiction = %s
                        GROUP BY status
                        """,
                        (jurisdiction,),
                    )
                    run_counts = {row["status"]: int(row["count"]) for row in cur.fetchall()}
                    complete_runs = run_counts.get("COMPLETE", 0)
                    running_runs = run_counts.get("RUNNING", 0)
                    failed_runs = run_counts.get("FAILED", 0)

                jurisdictions.append(
                    JurisdictionReadiness(
                        jurisdiction=jurisdiction,
                        store_schema=plan.store_schema,
                        schema_ready=not missing,
                        missing_relations=missing,
                        configured_sources=len(plan.sources),
                        active_sources=sum(source.active_now for source in plan.sources),
                        pipeline_ready_sources=sum(source.pipeline_ready for source in plan.sources),
                        authoritative_ready_sources=sum(
                            source.authoritative and source.pipeline_ready for source in plan.sources
                        ),
                        source_objects=source_objects,
                        complete_runs=complete_runs,
                        running_runs=running_runs,
                        failed_runs=failed_runs,
                    )
                )

    return ReadinessAudit(
        acquisition_schema_ready=acquisition_ready,
        missing_acquisition_relations=tuple(
            relation for relation, exists in acquisition_presence.items() if not exists
        ),
        jurisdictions=tuple(jurisdictions),
    )
