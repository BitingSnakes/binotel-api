import asyncio
import json
from typing import Any, cast

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope

from binotel_api.webhooks import ReceivedTheCall, create_webhook_router


class CaptureReceivedCall(ReceivedTheCall):
    async def handle(self, data):
        return {"general_call_id": data.general_call_id, "phone": data.external_number}


def make_app(*, custom_action: bool = True, allowed_ip: str = "testclient") -> FastAPI:
    app = FastAPI()
    actions = {"receivedTheCall": CaptureReceivedCall} if custom_action else None
    app.include_router(create_webhook_router(actions, allowed_ips={allowed_ip}))
    return app


def post_json(
    app: FastAPI, payload: dict[str, Any], *, client_ip: str = "testclient"
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
            "headers": [(b"content-type", b"application/json")],
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
