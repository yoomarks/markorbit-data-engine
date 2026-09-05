from __future__ import annotations

from typing import Any

from app.admin_progress import domain_progress_snapshot
from app.integration_contract import CONTRACT_VERSION, SOURCE_OWNER
from app.main_core import health
from app.operations_v2 import operations_snapshot
from app.version import engine_version


def owner_summary() -> dict[str, Any]:
    """Project bounded owner-local operational facts for Integration consumers.

    The Admin snapshots remain owner-local implementation detail. This projection
    deliberately exposes only aggregate/currentness facts and never forwards
    operation records, identifiers, filenames, errors, subtasks, metrics, or
    mutation guidance.
    """

    dependency_health = health()
    dependencies_ok = all(
        dependency_health.get(name) == "ok" for name in ("api", "postgres", "clickhouse")
    )
    operations = operations_snapshot()
    progress = domain_progress_snapshot()

    return {
        "contract_version": CONTRACT_VERSION,
        "engine_version": engine_version(),
        "source_owner": SOURCE_OWNER,
        "authority": "DATA_ENGINE_FACT_READ_MODEL",
        "read_only": True,
        "generated_at": progress["generated_at"],
        "health": {"status": "ok" if dependencies_ok else "degraded"},
        "operations": {
            "version": operations["version"],
            "action_authority": operations["action_authority"],
            "summary": dict(operations["summary"]),
        },
        "domain_progress": {
            "version": progress["version"],
            "active_count": int(progress["active_count"]),
        },
    }
