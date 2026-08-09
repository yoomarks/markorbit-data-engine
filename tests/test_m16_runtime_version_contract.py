from pathlib import Path

from app.version import engine_version


def test_engine_release_marker_is_m16_and_runtime_reads_it() -> None:
    version = Path("VERSION").read_text(encoding="utf-8").strip()
    assert version == "M1.6"
    assert engine_version() == version


def test_runtime_image_copies_version_marker() -> None:
    dockerfile = Path("docker/api.Dockerfile").read_text(encoding="utf-8")
    assert "COPY VERSION /app/VERSION" in dockerfile


def test_api_metadata_and_surfaces_use_current_engine_version() -> None:
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert "ENGINE_VERSION = engine_version()" in source
    assert '"version": ENGINE_VERSION' in source
    assert "M1.5" not in source
    assert "cn_goods_item_current" in source
    assert "cn_goods_item_observation" in source
    assert "cn_goods_scope_lifecycle_current" in source
    assert '"goods_items"' in source
    assert '"goods_lifecycle"' in source


def test_current_docs_identify_m16() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    architecture = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    build_validation = Path("docs/BUILD_VALIDATION.md").read_text(encoding="utf-8")
    changelog = Path("docs/CHANGELOG.md").read_text(encoding="utf-8")
    assert readme.startswith("# MarkOrbit Data Engine — M1.6")
    assert architecture.startswith("# MarkOrbit Data Engine M1.6 Architecture")
    assert build_validation.startswith("# M1.6 Build Validation")
    assert "## M1.6 / 0.4.0" in changelog
    assert "monthly omission is never deletion" in architecture.lower()


def test_m16_runtime_validation_checks_durable_goods_contract() -> None:
    script = Path("scripts/validate-m16.ps1").read_text(encoding="utf-8")
    assert '$health.version -ne "M1.6"' in script
    for field in (
        "cn_goods_item_current.goods_item_key",
        "cn_goods_item_current.operational_effect",
        "cn_goods_item_current.first_source_package_id",
        "cn_goods_item_observation.transition_type",
        "cn_goods_scope_lifecycle_current.all_known_goods_inactive",
        "cn_goods_scope_lifecycle_current.all_known_goods_final_inactive",
        "cn_goods_scope_lifecycle_current.code_2_item_count",
    ):
        assert field in script


def test_m16_reset_keeps_persistent_worker_stopped() -> None:
    script = Path("scripts/reset-m16.ps1").read_text(encoding="utf-8")
    assert "docker compose up -d --build postgres clickhouse api" in script
    assert "docker compose up -d --build postgres clickhouse api worker" not in script
    assert "validate-m16.ps1" in script


def test_m15_named_scripts_are_explicit_legacy_delegates() -> None:
    reset = Path("scripts/reset-m15.ps1").read_text(encoding="utf-8")
    validate = Path("scripts/validate-m15.ps1").read_text(encoding="utf-8")
    assert "legacy entry point" in reset
    assert "reset-m16.ps1" in reset
    assert "legacy entry point" in validate
    assert "validate-m16.ps1" in validate
