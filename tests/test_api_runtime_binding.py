from pathlib import Path

import pytest

from app.api_runtime import DEFAULT_API_PUBLISH_ADDRESS, validate_api_publish_configuration
from app.integration_security import MIN_API_KEY_LENGTH


VALID_KEY = "a" * MIN_API_KEY_LENGTH


def test_api_publish_defaults_to_host_loopback() -> None:
    assert validate_api_publish_configuration({}) == DEFAULT_API_PUBLISH_ADDRESS


@pytest.mark.parametrize("address", ["0.0.0.0", "::", "192.168.1.25"])
def test_non_loopback_publish_fails_without_explicit_opt_in(address: str) -> None:
    with pytest.raises(RuntimeError, match="API_ALLOW_REMOTE_BIND=true"):
        validate_api_publish_configuration({"API_PUBLISH_ADDRESS": address})


def test_non_loopback_publish_requires_read_and_control_authentication() -> None:
    with pytest.raises(RuntimeError, match="INTEGRATION_AUTH_MODE=required"):
        validate_api_publish_configuration(
            {
                "API_PUBLISH_ADDRESS": "0.0.0.0",
                "API_ALLOW_REMOTE_BIND": "true",
                "INTEGRATION_API_KEYS": VALID_KEY,
            }
        )

    with pytest.raises(RuntimeError, match="INTEGRATION_API_KEYS"):
        validate_api_publish_configuration(
            {
                "API_PUBLISH_ADDRESS": "0.0.0.0",
                "API_ALLOW_REMOTE_BIND": "true",
                "INTEGRATION_AUTH_MODE": "required",
                "INTEGRATION_API_KEYS": "too-short",
            }
        )


def test_explicit_authenticated_non_loopback_publish_is_accepted() -> None:
    assert (
        validate_api_publish_configuration(
            {
                "API_PUBLISH_ADDRESS": "0.0.0.0",
                "API_ALLOW_REMOTE_BIND": "true",
                "INTEGRATION_AUTH_MODE": "required",
                "INTEGRATION_API_KEYS": VALID_KEY,
            }
        )
        == "0.0.0.0"
    )


def test_compose_and_image_use_the_guarded_publish_contract() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = Path("docker/api.Dockerfile").read_text(encoding="utf-8")

    assert 'host_ip: "${API_PUBLISH_ADDRESS:-127.0.0.1}"' in compose
    assert 'published: "${API_PORT:-8080}"' in compose
    assert "API_ALLOW_REMOTE_BIND: ${API_ALLOW_REMOTE_BIND:-false}" in compose
    assert 'CMD ["python", "-m", "app.api_runtime"]' in dockerfile
