from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import clickhouse_connect

from app.config import Settings
from app.read_performance_baseline import QueryBenchmarkCase, run_benchmark_suite


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _identifier(value: str, label: str, pattern: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(pattern, normalized):
        raise ValueError(f"invalid {label}")
    return normalized


def _case(
    capability_id: str,
    jurisdiction: str,
    query_shape: str,
    sql: str,
    outcome: str,
    slo_ms: int | None,
    repetitions: int,
) -> QueryBenchmarkCase:
    return QueryBenchmarkCase(
        capability_id=capability_id,
        jurisdiction=jurisdiction,
        query_shape=query_shape,
        sql=sql,
        expected_outcome=outcome,
        slo_p95_ms=slo_ms,
        repetitions=repetitions if outcome == "SUCCESS" else 1,
    )


def cn_cases(args: argparse.Namespace) -> list[QueryBenchmarkCase]:
    application = _identifier(args.application, "CN application number", r"[0-9A-Za-z-]{1,32}")
    after_application = _identifier(
        args.after_application,
        "CN cursor application number",
        r"[0-9A-Za-z-]{1,32}",
    )
    after_relation_key = _identifier(
        args.after_relation_key, "CN cursor relation key", r"[0-9a-f]{64}"
    )
    entity_id = _identifier(
        args.entity_id,
        "CN entity id",
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    )
    name = _literal(args.normalized_name)
    agent_name = _literal(args.agent_name)
    app = _literal(application)
    return [
        _case(
            "exact_trademark_application",
            "CN",
            "application_number equality on primary key",
            f"SELECT application_number, filing_date, classes FROM markorbit_facts.cn_case_current FINAL WHERE application_number = {app} AND is_deleted = 0 LIMIT 1",
            "SUCCESS",
            150,
            args.repetitions,
        ),
        _case(
            "applicant_owner_name_resolve",
            "CN",
            "exact canonical name first page on lookup primary key",
            f"SELECT normalized_name, toString(entity_id), application_number FROM markorbit_facts.cn_applicant_name_lookup_current FINAL WHERE normalized_name = {name} AND is_deleted = 0 ORDER BY normalized_name, entity_id, application_number, relation_key LIMIT 51",
            "SUCCESS",
            300,
            args.repetitions,
        ),
        _case(
            "applicant_owner_name_resolve_cursor_page",
            "CN",
            "exact canonical name keyset page after entity and application",
            f"SELECT normalized_name, toString(entity_id), application_number FROM markorbit_facts.cn_applicant_name_lookup_current FINAL WHERE normalized_name = {name} AND (entity_id, application_number, relation_key) > (toUUID('{entity_id}'), '{after_application}', '{after_relation_key}') AND is_deleted = 0 ORDER BY normalized_name, entity_id, application_number, relation_key LIMIT 51",
            "SUCCESS",
            200,
            args.repetitions,
        ),
        _case(
            "agent_attorney_name_resolve",
            "CN",
            "exact agent normalized name without aligned key",
            f"SELECT agent_code, agent_name FROM markorbit_facts.cn_agent_current FINAL WHERE agent_name_norm = {agent_name} AND is_deleted = 0 ORDER BY agent_code LIMIT 51",
            "SUCCESS",
            300,
            args.repetitions,
        ),
        _case(
            "current_entity_portfolio",
            "CN",
            "entity_id equality against application-number ordered party table",
            f"SELECT application_number, role, relation_key FROM markorbit_facts.cn_case_party_current FINAL WHERE entity_id = toUUID('{entity_id}') AND is_current = 1 AND is_deleted = 0 ORDER BY application_number LIMIT 51",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "filing_date_range",
            "CN",
            "filing date range without aligned key",
            "SELECT application_number, filing_date FROM markorbit_facts.cn_case_current FINAL WHERE filing_date >= toDate32('2025-01-01') AND filing_date < toDate32('2025-02-01') AND is_deleted = 0 ORDER BY filing_date, application_number LIMIT 51",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "status_class_filtered_list",
            "CN",
            "class membership without aligned key",
            "SELECT application_number, classes FROM markorbit_facts.cn_case_current FINAL WHERE has(classes, toUInt8(35)) AND is_deleted = 0 ORDER BY application_number LIMIT 51",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "relationship_timeline",
            "CN",
            "application timeline against event-hash ordered history",
            f"SELECT application_number, event_type, event_date, observed_at FROM markorbit_facts.cn_observed_event FINAL WHERE application_number = {app} ORDER BY event_date, observed_at LIMIT 101",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "trademark_360",
            "CN",
            "case plus party plus event expansion",
            f"SELECT family, resource_id FROM (SELECT 'CASE' AS family, application_number AS resource_id FROM markorbit_facts.cn_case_current FINAL WHERE application_number = {app} AND is_deleted = 0 UNION ALL SELECT 'PARTY' AS family, application_number AS resource_id FROM markorbit_facts.cn_case_party_current FINAL WHERE application_number = {app} AND is_deleted = 0 UNION ALL SELECT 'EVENT' AS family, application_number AS resource_id FROM markorbit_facts.cn_observed_event FINAL WHERE application_number = {app}) LIMIT 201",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
    ]


def us_cases(args: argparse.Namespace) -> list[QueryBenchmarkCase]:
    serial = _identifier(args.serial, "US serial number", r"[0-9]{8}")
    after_serial = _identifier(args.after_serial, "US cursor serial number", r"[0-9]{8}")
    after_owner_key = _identifier(args.after_owner_key, "US cursor owner key", r"[0-9a-f]{64}")
    registration = _identifier(args.registration, "US registration number", r"[0-9]{1,12}")
    candidate_key = _identifier(args.candidate_key, "US candidate key", r"[0-9a-f]{64}")
    name = _literal(args.normalized_name)
    attorney = _literal(args.attorney_name)
    serial_sql = _literal(serial)
    return [
        _case(
            "exact_trademark_application",
            "US",
            "serial_number equality on primary key",
            f"SELECT serial_number, registration_number, filing_date, status_code FROM markorbit_facts.us_case_current FINAL WHERE serial_number = {serial_sql} AND is_deleted = 0 LIMIT 1",
            "SUCCESS",
            150,
            args.repetitions,
        ),
        _case(
            "exact_trademark_registration",
            "US",
            "registration_number equality without aligned key",
            f"SELECT serial_number, registration_number FROM markorbit_facts.us_case_current FINAL WHERE registration_number = '{registration}' AND is_deleted = 0 LIMIT 1",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "applicant_owner_name_resolve",
            "US",
            "exact canonical name first page on lookup primary key",
            f"SELECT normalized_name, toString(candidate_key), serial_number FROM markorbit_facts.us_applicant_name_lookup_current FINAL WHERE normalized_name = {name} AND is_deleted = 0 ORDER BY normalized_name, candidate_key, serial_number, owner_key LIMIT 51",
            "SUCCESS",
            300,
            args.repetitions,
        ),
        _case(
            "current_entity_portfolio",
            "US",
            "candidate key equality first page on primary key",
            f"SELECT toString(candidate_key), serial_number, owner_key FROM markorbit_facts.us_applicant_candidate_current FINAL WHERE candidate_key = '{candidate_key}' AND is_deleted = 0 ORDER BY candidate_key, serial_number, owner_key LIMIT 51",
            "SUCCESS",
            300,
            args.repetitions,
        ),
        _case(
            "current_entity_portfolio_cursor_page",
            "US",
            "candidate key equality keyset page after serial",
            f"SELECT toString(candidate_key), serial_number, owner_key FROM markorbit_facts.us_applicant_candidate_current FINAL WHERE candidate_key = '{candidate_key}' AND (serial_number, owner_key) > ('{after_serial}', '{after_owner_key}') AND is_deleted = 0 ORDER BY candidate_key, serial_number, owner_key LIMIT 51",
            "SUCCESS",
            200,
            args.repetitions,
        ),
        _case(
            "agent_attorney_name_resolve",
            "US",
            "attorney name equality without aligned key",
            f"SELECT serial_number, attorney_name FROM markorbit_facts.us_correspondent_current FINAL WHERE attorney_name = {attorney} AND is_deleted = 0 ORDER BY serial_number LIMIT 51",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "historical_entity_portfolio",
            "US",
            "Assignment assignee name equality without identity lookup",
            f"SELECT reel_frame_id, party_name, execution_date FROM markorbit_facts.us_assignment_assignee_history WHERE lowerUTF8(party_name) = {name} ORDER BY reel_frame_id LIMIT 51",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "filing_date_range",
            "US",
            "filing date range without aligned key",
            "SELECT serial_number, filing_date FROM markorbit_facts.us_case_current FINAL WHERE filing_date >= toDate32('2025-01-01') AND filing_date < toDate32('2025-02-01') AND is_deleted = 0 ORDER BY filing_date, serial_number LIMIT 51",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "status_class_filtered_list",
            "US",
            "status and class filters without aligned key",
            "SELECT c.serial_number, c.status_code, k.primary_code FROM markorbit_facts.us_case_current AS c FINAL INNER JOIN markorbit_facts.us_classification_current AS k FINAL ON k.serial_number = c.serial_number WHERE c.status_code = '700' AND k.primary_code = '035' AND c.is_deleted = 0 AND k.is_deleted = 0 ORDER BY c.serial_number LIMIT 51",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "relationship_timeline",
            "US",
            "serial timeline against event-key ordered history",
            f"SELECT serial_number, event_code, event_date, observed_at FROM markorbit_facts.us_event_history FINAL WHERE serial_number = {serial_sql} ORDER BY event_date, event_sequence LIMIT 101",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "trademark_360",
            "US",
            "case plus owner plus event expansion",
            f"SELECT family, resource_id FROM (SELECT 'CASE' AS family, serial_number AS resource_id FROM markorbit_facts.us_case_current FINAL WHERE serial_number = {serial_sql} AND is_deleted = 0 UNION ALL SELECT 'OWNER' AS family, serial_number AS resource_id FROM markorbit_facts.us_owner_current FINAL WHERE serial_number = {serial_sql} AND is_deleted = 0 UNION ALL SELECT 'EVENT' AS family, serial_number AS resource_id FROM markorbit_facts.us_event_history FINAL WHERE serial_number = {serial_sql}) LIMIT 201",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "assignment_lookup",
            "US",
            "current API serial lookup with corpus-wide latest-record aggregation",
            f"WITH latest_record AS (SELECT reel_frame_id, argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id FROM markorbit_facts.us_assignment_record_history GROUP BY reel_frame_id), linked AS (SELECT DISTINCT p.reel_frame_id FROM markorbit_facts.us_assignment_property_history AS p INNER JOIN latest_record AS lr ON p.reel_frame_id = lr.reel_frame_id AND toString(p.source_package_id) = lr.package_id WHERE p.serial_number = {serial_sql}) SELECT r.reel_frame_id, r.recorded_date FROM markorbit_facts.us_assignment_record_history AS r INNER JOIN latest_record AS lr ON r.reel_frame_id = lr.reel_frame_id AND toString(r.source_package_id) = lr.package_id INNER JOIN linked AS l ON r.reel_frame_id = l.reel_frame_id ORDER BY r.recorded_date DESC NULLS LAST, r.source_rank DESC, r.reel_frame_id DESC LIMIT 101",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
        _case(
            "ttab_lookup",
            "US",
            "current API serial lookup with corpus-wide latest-proceeding aggregation",
            f"WITH latest AS (SELECT proceeding_number, argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id FROM markorbit_facts.us_ttab_proceeding_history GROUP BY proceeding_number) SELECT p.proceeding_number, r.proceeding_type, r.filing_date, r.status_code FROM markorbit_facts.us_ttab_property_history AS p INNER JOIN latest AS l ON p.proceeding_number = l.proceeding_number AND toString(p.source_package_id) = l.package_id INNER JOIN markorbit_facts.us_ttab_proceeding_history AS r ON r.proceeding_number = p.proceeding_number AND r.source_package_id = p.source_package_id WHERE p.serial_number = {serial_sql} ORDER BY r.filing_date DESC NULLS LAST, r.source_rank DESC, p.proceeding_number DESC LIMIT 101",
            "BUDGET_REJECTED",
            None,
            args.repetitions,
        ),
    ]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the bounded Foundation read baseline")
    result.add_argument("--target", choices=("CN", "US"), required=True)
    result.add_argument("--repetitions", type=int, default=7)
    result.add_argument("--env-file", type=Path, default=Path(".env"))
    result.add_argument("--application", default="10020183")
    result.add_argument("--after-application", default="10090099")
    result.add_argument("--after-relation-key", default="0" * 64)
    result.add_argument("--entity-id", default="45639967-122c-5f40-897b-594e40112c3d")
    result.add_argument("--normalized-name", default="")
    result.add_argument("--agent-name", default="沈阳市商标事务所")
    result.add_argument("--serial", default="90000001")
    result.add_argument("--after-serial", default="76025479")
    result.add_argument("--after-owner-key", default="0" * 64)
    result.add_argument("--registration", default="7265548")
    result.add_argument(
        "--candidate-key",
        default="00c913dcb2b50551dd82b25d13c31bff8c48d2db37d8a8947871f0333b7a9e55",
    )
    result.add_argument("--attorney-name", default="Kathryn E. Smith")
    return result


def main() -> int:
    args = parser().parse_args()
    if not 1 <= args.repetitions <= 20:
        raise ValueError("repetitions must be between 1 and 20")
    if not args.normalized_name:
        args.normalized_name = (
            "腾讯科技深圳有限公司" if args.target == "CN" else "amazon technologies, inc."
        )

    if args.target == "CN":
        settings = Settings(_env_file=args.env_file)
        connection = {
            "host": settings.clickhouse_host,
            "port": settings.clickhouse_http_port,
            "username": settings.clickhouse_user,
            "password": settings.clickhouse_password,
            "database": settings.clickhouse_db,
        }
        cases = cn_cases(args)
        target = "production-cn"
    else:
        connection = {
            "host": "127.0.0.1",
            "port": 28123,
            "username": "default",
            "password": "",
            "database": "markorbit_facts",
        }
        cases = us_cases(args)
        target = "accepted-us-target"

    client = clickhouse_connect.get_client(
        **connection,
        send_receive_timeout=15,
        settings={"max_threads": 1},
    )
    try:
        result = run_benchmark_suite(client, cases, target=target)
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_expectations_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
