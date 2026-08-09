from pathlib import Path


def test_ci_builds_and_imports_runtime_image() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "runtime-image:" in workflow
    assert "docker compose config --quiet" in workflow
    assert "docker build --file docker/api.Dockerfile" in workflow
    assert "from app.main import ENGINE_VERSION" in workflow
    assert "ENGINE_VERSION == 'M1.6'" in workflow
