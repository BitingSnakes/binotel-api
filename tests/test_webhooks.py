import asyncio
import json
from typing import Any, cast

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope

from binotel_api.webhooks import ReceivedTheCall, WebhookConfig, create_webhook_router


class CaptureReceivedCall(ReceivedTheCall):
    async def handle(self, data):
        return {"general_call_id": data.general_call_id, "phone": data.external_number}


def make_app(*, custom_action: bool = True, allowed_ip: str = "testclient") -> FastAPI:
    app = FastAPI()
    actions = {"receivedTheCall": CaptureReceivedCall} if custom_action else None
    app.include_router(create_webhook_router(actions, allowed_ips={allowed_ip}))
    return app


def post_json(
    app: FastAPI,
    payload: dict[str, Any],
    *,
    client_ip: str = "testclient",
    forwarded_for: str | None = None,
) -> tuple[int, Any]:
    body = json.dumps(payload).encode()
    incoming: list[Message] = [
        {"type": "http.request", "body": body, "more_body": False},
    ]
    outgoing: list[Message] = []

    async def receive() -> Message:
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        outgoing.append(message)

    headers = [(b"content-type", b"application/json")]
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))

    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/binotel-api/webhook",
            "raw_path": b"/binotel-api/webhook",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": (client_ip, 1234),
            "server": ("testserver", 80),
        },
    )
    asyncio.run(app(scope, receive, send))

    status = next(
        message["status"] for message in outgoing if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"") for message in outgoing if message["type"] == "http.response.body"
    )
    return status, json.loads(response_body)


def test_webhook_dispatches_typed_payload() -> None:
    status, response = post_json(
        make_app(),
        {
            "requestType": "receivedTheCall",
            "generalCallID": 10,
            "callType": 0,
            "companyID": 20,
            "method": "phone",
            "externalNumber": "0671234567",
        },
    )

    assert status == 200
    assert response == {"general_call_id": 10, "phone": "+380671234567"}


def test_webhook_rejects_unknown_action() -> None:
    status, _ = post_json(make_app(), {"requestType": "unknown"})
    assert status == 404


@pytest.mark.parametrize(
    ("request_type", "payload"),
    [
        (
            "apiCallSettings",
            {
                "externalNumber": "0671234567",
                "companyID": 1,
                "callType": 0,
            },
        ),
        ("apiCallCompleted", {"callDetails": []}),
        (
            "receivedTheCall",
            {"generalCallID": 1, "callType": 0, "companyID": 1, "method": "phone"},
        ),
        ("answeredTheCall", {"generalCallID": 1, "callType": 0, "companyID": 1}),
        (
            "hangupTheCall",
            {"generalCallID": 1, "billsec": 3, "disposition": "ANSWER", "method": "phone"},
        ),
        ("transferredTheCall", {"internalNumber": 801, "generalCallID": 1, "companyID": 1}),
    ],
)
def test_default_webhook_actions_accept_original_payloads(
    request_type: str, payload: dict[str, Any]
) -> None:
    status, response = post_json(
        make_app(custom_action=False), {"requestType": request_type, **payload}
    )

    assert status == 200
    assert response == {}


def test_webhook_rejects_unapproved_ip() -> None:
    status, _ = post_json(
        make_app(allowed_ip="203.0.113.1"),
        {"requestType": "apiCallCompleted", "callDetails": []},
    )

    assert status == 403


def test_webhook_accepts_programmatic_allowlist() -> None:
    app = FastAPI()
    app.include_router(
        create_webhook_router(
            config=WebhookConfig(allowed_ips=frozenset({"203.0.113.10"})),
        )
    )

    status, _ = post_json(
        app,
        {"requestType": "apiCallCompleted", "callDetails": []},
        client_ip="203.0.113.10",
    )

    assert status == 200


def test_webhook_loads_allowlist_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BINOTEL_WEBHOOK_ALLOWED_IPS",
        " 203.0.113.10, 198.51.100.20 ",
    )
    app = FastAPI()
    app.include_router(create_webhook_router())

    accepted, _ = post_json(
        app,
        {"requestType": "apiCallCompleted", "callDetails": []},
        client_ip="198.51.100.20",
    )
    rejected, _ = post_json(
        app,
        {"requestType": "apiCallCompleted", "callDetails": []},
        client_ip="192.0.2.1",
    )

    assert accepted == 200
    assert rejected == 403


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_webhook_config_parses_true_environment_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("BINOTEL_WEBHOOK_TRUST_FORWARDED_FOR", value)

    assert WebhookConfig.from_env().trust_forwarded_for is True


def test_environment_config_can_trust_forwarded_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BINOTEL_WEBHOOK_ALLOWED_IPS", "203.0.113.10")
    monkeypatch.setenv("BINOTEL_WEBHOOK_TRUST_FORWARDED_FOR", "true")
    app = FastAPI()
    app.include_router(create_webhook_router())

    status, _ = post_json(
        app,
        {"requestType": "apiCallCompleted", "callDetails": []},
        client_ip="192.0.2.1",
        forwarded_for="203.0.113.10",
    )

    assert status == 200


def test_explicit_allowlist_overrides_configuration() -> None:
    app = FastAPI()
    app.include_router(
        create_webhook_router(
            config=WebhookConfig(allowed_ips=frozenset({"192.0.2.1"})),
            allowed_ips={"203.0.113.10"},
        )
    )

    accepted, _ = post_json(
        app,
        {"requestType": "apiCallCompleted", "callDetails": []},
        client_ip="203.0.113.10",
    )

    assert accepted == 200
