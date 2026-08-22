from __future__ import annotations

from app.trademark_framework.http_transport import HttpRequestSpec, HttpTransportError


def main() -> int:
    spec = HttpRequestSpec(
        url="https://authority.example/api?api_key=url-secret&cursor=page-secret",
        headers={
            "Authorization": "Bearer header-secret",
            "X-Api-Key": "header-api-secret",
        },
    )

    rendered = repr(spec)
    for secret in ("url-secret", "page-secret", "header-secret", "header-api-secret"):
        assert secret not in rendered

    error = HttpTransportError(
        reason="non-retryable HTTP status",
        safe_url="https://authority.example/api",
        attempts=1,
        status_code=401,
    )
    rendered_error = str(error)
    assert "authority.example/api" in rendered_error
    assert "?" not in rendered_error
    assert "url-secret" not in rendered_error
    assert "header-secret" not in rendered_error

    print(
        {
            "status": "PASS",
            "request_repr_redacts_url": True,
            "request_repr_redacts_headers": True,
            "error_url_has_no_query": True,
            "credential_values_not_rendered": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
