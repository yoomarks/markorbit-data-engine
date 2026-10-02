from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI

from app import admin_task_api, integration_security, main_core
from app.contact_ingest import admin_api as contact_admin_api
from app.main import app as full_app


API_KEY = "a" * integration_security.MIN_API_KEY_LENGTH


def _settings(*, mode: str = "disabled", keys: str = API_KEY) -> SimpleNamespace:
    return SimpleNamespace(
        integration_auth_mode=mode,
        integration_api_keys=keys,
    )


def _control_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_task_api.router)
    app.include_router(contact_admin_api.router)
    return app


async def _asgi_request(
    app: FastAPI,
    method: str,
    target: str,
    *,
    authorization: str | None = None,
) -> tuple[int, dict]:
    parsed = urlsplit(target)
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"),
        "headers": headers,
        "client": ("203.0.113.10", 40123),
        "server": ("data-engine.example", 80),
        "root_path": "",
    }
    request_sent = False
    messages: list[dict] = []

    async def receive() -> dict:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(body or b"{}")


def _request(
    app: FastAPI,
    method: str,
    target: str,
    *,
    authorization: str | None = None,
) -> tuple[int, dict]:
    return asyncio.run(_asgi_request(app, method, target, authorization=authorization))


def test_every_admin_control_write_has_mandatory_auth_dependency() -> None:
    for router in (admin_task_api.router, contact_admin_api.router):
        for route in router.routes:
            if "POST" not in route.methods:
                continue
            dependency_calls = [dependency.call for dependency in route.dependant.dependencies]
            assert integration_security.require_admin_control_auth in dependency_calls, route.path


def test_unauthenticated_control_writes_reject_before_any_mutation(monkeypatch) -> None:
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings(),
    )

    def unexpected_mutation(*args, **kwargs):
        raise AssertionError(f"mutation reached: args={args!r} kwargs={kwargs!r}")

    for module, names in (
        (
            admin_task_api,
            (
                "queue_admin_domain_task",
                "request_admin_domain_stop",
                "approve_target_bulk_task",
                "resume_target_bulk_task",
            ),
        ),
        (
            contact_admin_api,
            ("scan_contact_incoming", "list_contact_tasks", "get_contact_task"),
        ),
    ):
        for name in names:
            monkeypatch.setattr(module, name, unexpected_mutation)

    app = _control_app()
    targets = (
        "/api/admin/v2/domain-tasks/CN/RUN",
        "/api/admin/v2/domain-tasks/CN/STOP",
        "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/run-1/APPROVE?plan_sha256=" + "b" * 64,
        "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/run-1/RESUME",
        "/api/admin/contacts/scan",
        "/api/admin/contacts/tasks/batch-apply",
        "/api/admin/contacts/tasks/task-1/apply",
    )

    for target in targets:
        status, body = _request(app, "POST", target)
        assert status == 401, target
        assert body["detail"]["code"] == "DATA_ENGINE_INTEGRATION_AUTH_REQUIRED"


def test_unauthenticated_legacy_job_controls_reject_before_any_mutation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings(),
    )

    def unexpected_mutation(*args, **kwargs):
        raise AssertionError(f"mutation reached: args={args!r} kwargs={kwargs!r}")

    for name in (
        "_cn_api_execution_guard",
        "scan_cn_incoming",
        "scan_and_ingest_cn",
        "ingest_pending_cn",
    ):
        monkeypatch.setattr(main_core, name, unexpected_mutation)

    for target in (
        "/api/jobs/cn/scan",
        "/api/jobs/cn/run",
        "/api/jobs/cn/retry",
    ):
        status, body = _request(full_app, "POST", target)
        assert status == 401, target
        assert body["detail"]["code"] == "DATA_ENGINE_INTEGRATION_AUTH_REQUIRED"


def test_authorized_operator_can_use_each_control_route_family(monkeypatch) -> None:
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings(),
    )
    monkeypatch.setattr(
        admin_task_api,
        "queue_admin_domain_task",
        lambda **kwargs: {"operation": "domain", **kwargs},
    )
    monkeypatch.setattr(
        admin_task_api,
        "request_admin_domain_stop",
        lambda **kwargs: {"operation": "stop", **kwargs},
    )
    monkeypatch.setattr(
        admin_task_api,
        "approve_target_bulk_task",
        lambda **kwargs: {"operation": "approve", **kwargs},
    )
    monkeypatch.setattr(
        admin_task_api,
        "resume_target_bulk_task",
        lambda **kwargs: {"operation": "resume", **kwargs},
    )
    monkeypatch.setattr(
        contact_admin_api,
        "scan_contact_incoming",
        lambda: {"operation": "scan"},
    )
    monkeypatch.setattr(contact_admin_api, "list_contact_tasks", lambda **kwargs: [])
    monkeypatch.setattr(
        contact_admin_api,
        "get_contact_task",
        lambda task_id: {"task_id": task_id, "status": "SUCCESS"},
    )

    app = _control_app()
    authorization = f"Bearer {API_KEY}"
    targets = (
        ("/api/admin/v2/domain-tasks/CN/RUN", 202),
        ("/api/admin/v2/domain-tasks/CN/STOP", 202),
        (
            "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/run-1/APPROVE?plan_sha256=" + "b" * 64,
            202,
        ),
        ("/api/admin/v2/domain-tasks/US_APPLICATION/BULK/run-1/RESUME", 202),
        ("/api/admin/contacts/scan", 200),
        ("/api/admin/contacts/tasks/batch-apply", 202),
        ("/api/admin/contacts/tasks/task-1/apply", 202),
    )

    for target, expected_status in targets:
        status, _body = _request(
            app,
            "POST",
            target,
            authorization=authorization,
        )
        assert status == expected_status, target


def test_authorized_operator_can_use_legacy_job_controls(monkeypatch) -> None:
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings(),
    )
    monkeypatch.setattr(main_core, "_cn_api_execution_guard", lambda action: {"action": action})
    monkeypatch.setattr(
        main_core,
        "scan_cn_incoming",
        lambda **kwargs: {"operation": "scan", **kwargs},
    )
    monkeypatch.setattr(
        main_core,
        "scan_and_ingest_cn",
        lambda **kwargs: {"operation": "run", **kwargs},
    )
    monkeypatch.setattr(
        main_core,
        "ingest_pending_cn",
        lambda **kwargs: {"operation": "retry", **kwargs},
    )

    authorization = f"Bearer {API_KEY}"
    for target in (
        "/api/jobs/cn/scan",
        "/api/jobs/cn/run",
        "/api/jobs/cn/retry",
    ):
        status, _body = _request(
            full_app,
            "POST",
            target,
            authorization=authorization,
        )
        assert status == 200, target


def test_missing_or_invalid_auth_configuration_fails_closed(monkeypatch) -> None:
    app = _control_app()
    for settings in (
        _settings(keys=""),
        _settings(keys="too-short"),
        _settings(mode="unexpected"),
    ):
        monkeypatch.setattr(
            integration_security,
            "get_settings",
            lambda settings=settings: settings,
        )
        status, body = _request(
            app,
            "POST",
            "/api/admin/v2/domain-tasks/CN/RUN",
            authorization=f"Bearer {API_KEY}",
        )
        assert status == 503
        assert body["detail"]["code"] == "DATA_ENGINE_INTEGRATION_AUTH_CONFIGURATION_INVALID"


def test_read_only_admin_routes_keep_disabled_mode_compatibility(monkeypatch) -> None:
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings(keys=""),
    )
    monkeypatch.setattr(admin_task_api, "active_target_bulk_task", lambda: None)
    monkeypatch.setattr(admin_task_api, "resumable_target_bulk_task", lambda: None)
    monkeypatch.setattr(
        contact_admin_api,
        "contact_task_summary",
        lambda: {"status": "readable"},
    )
    app = _control_app()

    active_status, _active_body = _request(
        app,
        "GET",
        "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/ACTIVE",
    )
    summary_status, _summary_body = _request(
        app,
        "GET",
        "/api/admin/contacts/summary",
    )

    assert active_status == 200
    assert summary_status == 200
