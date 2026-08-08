from __future__ import annotations

import inspect

from app.cn import audit_monthly_patch


def test_monthly_patch_audit_uses_durable_observations_not_transient_stage() -> None:
    source = inspect.getsource(audit_monthly_patch.build_audit)

    assert "cn_goods_item_observation" in source
    assert "cn_stage_goods" not in source
    assert "CN_M16_MONTHLY_PATCH_POLICY_V5_DURABLE_OBSERVATION_RECONCILIATION" == (
        audit_monthly_patch.POLICY_VERSION
    )


def test_monthly_patch_audit_checks_first_source_lineage_and_omission() -> None:
    source = inspect.getsource(audit_monthly_patch.build_audit)

    assert "first_source_lineage_disagrees_with_first_observed_transitions" in source
    assert "first_source_lineage_disagrees_with_existing_item_transitions" in source
    assert "omitted_items_preserved" in source
    assert "durable_scope_has_fewer_items_than_patch" in source
