from pathlib import Path

from app.cn import CN_MODEL_VERSION
from app.cn.final_checkpoint import CHECKPOINT_VERSION as CN_CHECKPOINT_VERSION
from app.component_versions import (
    COMPONENT_MATRIX_VERSION,
    REPLAY_TELEMETRY_VERSION,
    STORAGE_POLICY_VERSION,
    component_versions,
)
from app.domain_lifecycle import LIFECYCLE_VERSION
from app.four_domain_acceptance import AUDIT_VERSION as FOUR_DOMAIN_ACCEPTANCE_VERSION
from app.global_trademarks.acceptance import GLOBAL_TRADEMARK_ACCEPTANCE_VERSION
from app.global_trademarks.migrations import GLOBAL_TRADEMARK_SCHEMA_VERSION
from app.global_trademarks.operator import GLOBAL_TRADEMARK_OPERATOR_VERSION
from app.integration_contract import CONTRACT_VERSION
from app.storage_headroom import HEADROOM_VERSION
from app.us.alert_engine import ALERT_ENGINE_VERSION
from app.us.migrations import US_SCHEMA_VERSION
from app.us_assignment import ASSIGNMENT_SCHEMA_VERSION
from app.us_ttab import TTAB_SCHEMA_VERSION
from app.version import engine_version


ROOT = Path(__file__).resolve().parents[1]


def test_component_matrix_is_derived_from_owner_constants() -> None:
    report = component_versions()
    components = report["components"]

    assert report["matrix_version"] == COMPONENT_MATRIX_VERSION
    assert report["engine_release"] == engine_version()
    assert components["cn"]["model_version"] == CN_MODEL_VERSION
    assert components["cn"]["final_checkpoint_version"] == CN_CHECKPOINT_VERSION
    assert components["us_application"]["schema_version"] == US_SCHEMA_VERSION
    assert components["us_assignment"]["schema_version"] == ASSIGNMENT_SCHEMA_VERSION
    assert components["us_ttab"]["schema_version"] == TTAB_SCHEMA_VERSION
    assert components["us_alert_engine"]["version"] == ALERT_ENGINE_VERSION
    assert components["global_trademark"]["schema_version"] == GLOBAL_TRADEMARK_SCHEMA_VERSION
    assert components["global_trademark"]["operator_version"] == GLOBAL_TRADEMARK_OPERATOR_VERSION
    assert (
        components["global_trademark"]["acceptance_version"]
        == GLOBAL_TRADEMARK_ACCEPTANCE_VERSION
    )
    assert components["storage"]["policy_version"] == STORAGE_POLICY_VERSION
    assert components["storage"]["headroom_version"] == HEADROOM_VERSION
    assert components["storage"]["replay_telemetry_version"] == REPLAY_TELEMETRY_VERSION
    assert components["integration"]["contract_version"] == CONTRACT_VERSION
    assert components["domain_lifecycle"]["version"] == LIFECYCLE_VERSION
    assert components["four_domain_acceptance"]["version"] == FOUR_DOMAIN_ACCEPTANCE_VERSION


def test_readme_and_component_version_doc_track_current_versions() -> None:
    documentation = "\n".join(
        [
            (ROOT / "README.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "COMPONENT_VERSIONS.md").read_text(encoding="utf-8"),
        ]
    )
    expected = {
        engine_version(),
        CN_MODEL_VERSION,
        US_SCHEMA_VERSION,
        ASSIGNMENT_SCHEMA_VERSION,
        TTAB_SCHEMA_VERSION,
        ALERT_ENGINE_VERSION,
        GLOBAL_TRADEMARK_SCHEMA_VERSION,
        GLOBAL_TRADEMARK_OPERATOR_VERSION,
        GLOBAL_TRADEMARK_ACCEPTANCE_VERSION,
        STORAGE_POLICY_VERSION,
        REPLAY_TELEMETRY_VERSION,
        CONTRACT_VERSION,
        LIFECYCLE_VERSION,
        FOUR_DOMAIN_ACCEPTANCE_VERSION,
    }
    for version in expected:
        assert version in documentation, version


def test_readme_no_longer_claims_us_m12_is_current() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "US M1.2 核心能力" not in readme
    assert "US M1.2 本地导入" not in readme
    assert "US Application M1.4" in readme
