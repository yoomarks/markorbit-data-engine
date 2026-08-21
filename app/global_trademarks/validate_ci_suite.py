from __future__ import annotations

from collections.abc import Callable

from app.global_trademarks import (
    validate_acceptance_fixture,
    validate_bounded_pilot_fixture,
    validate_cipo_weekly_fixture,
    validate_operator_fixture,
    validate_platform_hardening_fixture,
    validate_readiness_audit,
    validate_resumable_fixture,
    validate_source_identity_fixture,
    validate_upgrade_fixture,
)


GLOBAL_TRADEMARK_CI_SUITE_VERSION = "GLOBAL_TM_CI_SUITE_V2"


def _run(name: str, fixture: Callable[[], int]) -> None:
    print({"suite": GLOBAL_TRADEMARK_CI_SUITE_VERSION, "phase": name, "status": "START"})
    result = fixture()
    if result != 0:
        raise RuntimeError(f"global trademark CI fixture failed: {name} rc={result}")
    print({"suite": GLOBAL_TRADEMARK_CI_SUITE_VERSION, "phase": name, "status": "PASS"})


def main() -> int:
    # Upgrade must run first: it intentionally creates PR #181-shaped legacy AU/CA
    # tables before any current-schema migration touches this PostgreSQL volume.
    fixtures: tuple[tuple[str, Callable[[], int]], ...] = (
        ("legacy_upgrade_and_country_ingestion", validate_upgrade_fixture.main),
        ("platform_hardening", validate_platform_hardening_fixture.main),
        ("operator_contract", validate_operator_fixture.main),
        ("source_identity_pin", validate_source_identity_fixture.main),
        ("bounded_pilot", validate_bounded_pilot_fixture.main),
        ("durable_resume", validate_resumable_fixture.main),
        ("cipo_weekly_tombstones", validate_cipo_weekly_fixture.main),
        ("readiness_audit", validate_readiness_audit.main),
        ("release_acceptance", validate_acceptance_fixture.main),
    )
    for name, fixture in fixtures:
        _run(name, fixture)

    print(
        {
            "status": "PASS",
            "suite_version": GLOBAL_TRADEMARK_CI_SUITE_VERSION,
            "fixture_count": len(fixtures),
            "coverage_reduced": False,
            "container_invocations_consolidated": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
