"""FastAPI router for Binotel webhooks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Generic, TypeVar

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from .schemas import (
    AnsweredTheCallData,
    ApiCallCompletedData,
    ApiCallSettingsData,
    HangupTheCallData,
    ReceivedTheCallData,
    TransferredTheCallData,
)

ALLOWED_IPS = frozenset(
    {
        "194.88.218.116",
        "194.88.218.114",
        "194.88.218.117",
        "194.88.218.118",
        "194.88.219.67",
        "194.88.219.78",
        "194.88.219.70",
        "194.88.219.71",
        "194.88.219.72",
        "194.88.219.79",
        "194.88.219.80",
        "194.88.219.81",
        "194.88.219.82",
        "194.88.219.83",
        "194.88.219.84",
        "194.88.219.85",
        "194.88.219.86",
        "194.88.219.87",
        "194.88.219.88",
        "194.88.219.89",
        "194.88.219.92",
        "194.88.218.119",
        "194.88.218.120",
        "185.100.66.145",
        "185.100.66.146",
        "185.100.66.147",
        "45.91.130.51",
        "45.91.130.36",
    }
)

PayloadT = TypeVar("PayloadT", bound=BaseModel)


class WebhookAction(Generic[PayloadT]):
    """Override ``handle`` to perform application-specific webhook work."""

    payload_model: type[PayloadT]

    def transform(self, payload: Mapping[str, Any]) -> PayloadT:
        return self.payload_model.model_validate(payload)

    async def handle(self, data: PayloadT) -> dict[str, Any]:
        return {}


class ApiCallSettings(WebhookAction[ApiCallSettingsData]):
    payload_model = ApiCallSettingsData


class ApiCallCompleted(WebhookAction[ApiCallCompletedData]):
    payload_model = ApiCallCompletedData


class ReceivedTheCall(WebhookAction[ReceivedTheCallData]):
    payload_model = ReceivedTheCallData


class AnsweredTheCall(WebhookAction[AnsweredTheCallData]):
    payload_model = AnsweredTheCallData


class HangupTheCall(WebhookAction[HangupTheCallData]):
    payload_model = HangupTheCallData


class TransferredTheCall(WebhookAction[TransferredTheCallData]):
    payload_model = TransferredTheCallData


DEFAULT_ACTIONS: dict[str, type[WebhookAction[Any]]] = {
    "apiCallSettings": ApiCallSettings,
    "apiCallCompleted": ApiCallCompleted,
    "receivedTheCall": ReceivedTheCall,
    "answeredTheCall": AnsweredTheCall,
    "hangupTheCall": HangupTheCall,
    "transferredTheCall": TransferredTheCall,
}


def create_webhook_router(
    actions: Mapping[str, type[WebhookAction[Any]]] | None = None,
    *,
    allowed_ips: set[str] | frozenset[str] | None = None,
    trust_forwarded_for: bool = False,
) -> APIRouter:
    """Create the ``/binotel-api/webhook`` router.

    ``trust_forwarded_for`` should only be enabled behind a trusted proxy that
    replaces, rather than appends untrusted values to, ``X-Forwarded-For``.
    """

    router = APIRouter(prefix="/binotel-api", tags=["binotel"])
    action_map = dict(DEFAULT_ACTIONS if actions is None else actions)
    ip_allowlist = ALLOWED_IPS if allowed_ips is None else frozenset(allowed_ips)

    @router.post("/webhook")
    async def webhook(request: Request) -> dict[str, Any]:
        remote_ip = request.client.host if request.client else ""
        if trust_forwarded_for and request.headers.get("x-forwarded-for"):
            remote_ip = request.headers["x-forwarded-for"].split(",", 1)[0].strip()
        if remote_ip not in ip_allowlist:
            raise HTTPException(status_code=403, detail="Webhook source is not allowed")

        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Request body must be JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("requestType"), str):
            raise HTTPException(status_code=422, detail="requestType is required")

        action_class = action_map.get(payload["requestType"])
        if action_class is None:
            raise HTTPException(status_code=404, detail="Unknown webhook requestType")
        action = action_class()
        try:
            data = action.transform(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        return await action.handle(data)

    return router
