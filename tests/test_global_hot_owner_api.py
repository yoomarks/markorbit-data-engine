from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI

from app import fact_admission_security, integration_security
from app.global_trademarks import owner_api
from app.global_trademarks.hot_global_admission import CONTRACT_VERSION


@dataclass(frozen=True)
class AuthSettings:
    integration_auth_mode: str = "required"
    fact_admission_api_keys: str = "a" * 64
    integration_api_keys: str = "b" * 64


class ScopedClient:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def configure(monkeypatch, settings: AuthSettings | None = None):
    values = settings or AuthSettings()
    client = ScopedClient()
    checks: list[ScopedClient] = []
    monkeypatch.setattr(owner_api, "get_settings", lambda: values)
    monkeypatch.setattr(fact_admission_security, "get_settings", lambda: values)
    monkeypatch.setattr(integration_security, "get_settings", lambda: values)
    monkeypatch.setattr(owner_api, "clickhouse_client", lambda: client)
    monkeypatch.setattr(owner_api, "require_hot_global_ready", lambda value: checks.append(value))
    monkeypatch.setattr(owner_api, "require_wipo_mgs_ready", lambda value: checks.append(value))
    return client, checks


async def request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    bearer: str | None = None,
    payload: dict | None = None,
) -> tuple[int, dict]:
    """Minimal stdlib ASGI probe; no httpx/TestClient or actual network."""
    headers = []
    if bearer is not None:
        headers.append((b"authorization", ("Bearer " + bearer).encode("ascii")))
    body = b""
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
        body = json.dumps(payload).encode("utf-8")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": method,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "http_version": "1.1",
        "scheme": "http",
        "server": ("localhost", 18081),
        "client": ("127.0.0.1", 50001),
        "root_path": "",
        "state": {},
    }
    outgoing: list[dict] = []
    incoming = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message):
        outgoing.append(message)

    await app(scope, receive, send)
    starts = [message for message in outgoing if message["type"] == "http.response.start"]
    bodies = [
        message.get("body", b"") for message in outgoing if message["type"] == "http.response.body"
    ]
    assert len(starts) == 1
    return starts[0]["status"], json.loads(b"".join(bodies).decode("utf-8"))


def start_once(app: FastAPI) -> None:
    async def run():
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(run())


def test_owner_entrypoint_is_only_existing_global_routes_not_cn_runtime():
    paths = {route.path for route in owner_api.app.routes}
    assert paths == {
        "/api/admin/v2/fact-admissions/global/observations",
        "/api/admin/v2/fact-admissions/reference/wipo-mgs/snapshots",
        "/api/v1/global/trademarks/{jurisdiction}/{source_record_id}",
        "/api/v1/health/global-hot",
        "/api/v1/reference/wipo-mgs/terms",
        "/api/v1/reference/wipo-mgs/terms/{source_term_id}",
    }
    source = Path("app/global_trademarks/owner_api.py").read_text(encoding="utf-8")
    assert "main_core" not in source and "from app.main" not in source
    assert "reset-m15" not in source and "install_hot_global_schema" not in source


@pytest.mark.parametrize(
    "settings",
    [
        AuthSettings(integration_auth_mode="disabled"),
        AuthSettings(fact_admission_api_keys=""),
        AuthSettings(integration_api_keys=""),
        AuthSettings(fact_admission_api_keys="too-short"),
        AuthSettings(integration_api_keys="too-short"),
        AuthSettings(integration_api_keys="a" * 64),
    ],
)
def test_owner_refuses_missing_disabled_insecure_or_shared_credentials(monkeypatch, settings):
    scoped, checks = configure(monkeypatch, settings)
    with pytest.raises(RuntimeError):
        start_once(owner_api.create_global_hot_owner_app())
    assert scoped.closed == 0
    assert checks == []


def test_owner_storage_readiness_is_required_at_startup_and_client_closes(monkeypatch):
    scoped, _checks = configure(monkeypatch)

    def not_ready(_client):
        raise RuntimeError("hot_global disk is not mounted")

    monkeypatch.setattr(owner_api, "require_hot_global_ready", not_ready)
    with pytest.raises(RuntimeError, match="hot_global"):
        start_once(owner_api.create_global_hot_owner_app())
    assert scoped.closed == 1


def test_owner_uses_exact_global_routers_and_independent_bearer_scopes(monkeypatch):
    scoped, checks = configure(monkeypatch)
    app = owner_api.create_global_hot_owner_app()

    async def scenario():
        async with app.router.lifespan_context(app):
            assert checks == [scoped, scoped]
            status, body = await request(app, "GET", "/api/v1/health/global-hot")
            assert status == 200
            assert body == {
                "status": "ready",
                "owner": "GLOBAL_HOT",
                "contract_version": CONTRACT_VERSION,
                "storage_placement": "hot_global",
            }
            assert len(checks) == 4
            write = "/api/admin/v2/fact-admissions/global/observations"
            read = "/api/v1/global/trademarks/ZZ/LA55159"
            assert (await request(app, "POST", write, payload={}))[0] == 401
            assert (
                await request(
                    app, "POST", write, payload={}, bearer=AuthSettings().integration_api_keys
                )
            )[0] == 401
            status, body = await request(
                app, "POST", write, payload={}, bearer=AuthSettings().fact_admission_api_keys
            )
            assert status == 400
            assert body["detail"]["code"] == "GLOBAL_TRADEMARK_ADMISSION_REJECTED"
            assert (await request(app, "GET", read))[0] == 401
            assert (await request(app, "GET", read, bearer=AuthSettings().fact_admission_api_keys))[
                0
            ] == 401
            assert (await request(app, "GET", read, bearer=AuthSettings().integration_api_keys))[
                0
            ] == 404

    asyncio.run(scenario())
    assert scoped.closed == 2


def test_health_fails_closed_if_hot_global_becomes_unavailable_after_startup(monkeypatch):
    scoped, checks = configure(monkeypatch)
    app = owner_api.create_global_hot_owner_app()

    async def scenario():
        async with app.router.lifespan_context(app):
            assert checks == [scoped, scoped]

            def not_ready(_client):
                raise RuntimeError("private operational detail should not reach health callers")

            monkeypatch.setattr(owner_api, "require_hot_global_ready", not_ready)
            status, body = await request(app, "GET", "/api/v1/health/global-hot")
            assert status == 503
            assert body["detail"]["code"] == "GLOBAL_HOT_OWNER_NOT_READY"
            assert "private operational detail" not in json.dumps(body)

    asyncio.run(scenario())
    assert scoped.closed == 2
