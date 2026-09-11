"""FastAPI router for Binotel webhooks."""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Generic, Self, TypeVar

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


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    """Security settings for the Binotel webhook router."""

    allowed_ips: Collection[str] = ALLOWED_IPS
    trust_forwarded_for: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_ips", frozenset(self.allowed_ips))

    @classmethod
    def from_env(cls) -> Self:
        """Load webhook settings from environment variables."""
        raw_ips = os.getenv("BINOTEL_WEBHOOK_ALLOWED_IPS")
        allowed_ips = (
            ALLOWED_IPS
            if raw_ips is None
            else frozenset(value.strip() for value in raw_ips.split(",") if value.strip())
        )
        return cls(
            allowed_ips=allowed_ips,
            trust_forwarded_for=_env_bool("BINOTEL_WEBHOOK_TRUST_FORWARDED_FOR", False),
        )


PayloadT = TypeVar("PayloadT", bound=BaseModel)


class WebhookAction(Generic[PayloadT]):
    """Override ``handle`` to perform application-specific webhook work."""

    payload_model: type[PayloadT]

    def transform(self, payload: Mapping[str, Any]) -> PayloadT:
        """Validate a raw webhook mapping as the action's payload model."""
        return self.payload_model.model_validate(payload)

    async def handle(self, data: PayloadT) -> dict[str, Any]:
        """Handle a validated webhook payload and return a JSON response."""
        return {}


class ApiCallSettings(WebhookAction[ApiCallSettingsData]):
    """Handle the ``apiCallSettings`` webhook event."""

    payload_model = ApiCallSettingsData


class ApiCallCompleted(WebhookAction[ApiCallCompletedData]):
    """Handle the ``apiCallCompleted`` webhook event."""

    payload_model = ApiCallCompletedData


class ReceivedTheCall(WebhookAction[ReceivedTheCallData]):
    """Handle the ``receivedTheCall`` webhook event."""

    payload_model = ReceivedTheCallData


class AnsweredTheCall(WebhookAction[AnsweredTheCallData]):
    """Handle the ``answeredTheCall`` webhook event."""

    payload_model = AnsweredTheCallData


class HangupTheCall(WebhookAction[HangupTheCallData]):
    """Handle the ``hangupTheCall`` webhook event."""

    payload_model = HangupTheCallData


class TransferredTheCall(WebhookAction[TransferredTheCallData]):
    """Handle the ``transferredTheCall`` webhook event."""

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
    config: WebhookConfig | None = None,
    allowed_ips: Collection[str] | None = None,
    trust_forwarded_for: bool | None = None,
) -> APIRouter:
    """Create the ``/binotel-api/webhook`` router.

    Settings are loaded from the environment by default. Explicit arguments
    override values from ``config`` or the environment.

    ``trust_forwarded_for`` should only be enabled behind a trusted proxy that
    replaces, rather than appends untrusted values to, ``X-Forwarded-For``.
    """
    router = APIRouter(prefix="/binotel-api", tags=["binotel"])
    action_map = dict(DEFAULT_ACTIONS if actions is None else actions)
    settings = WebhookConfig.from_env() if config is None else config
    ip_allowlist = settings.allowed_ips if allowed_ips is None else frozenset(allowed_ips)
    use_forwarded_for = (
        settings.trust_forwarded_for if trust_forwarded_for is None else trust_forwarded_for
    )

    @router.post("/webhook")
    async def webhook(request: Request) -> dict[str, Any]:
        remote_ip = request.client.host if request.client else ""
        if use_forwarded_for and request.headers.get("x-forwarded-for"):
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
