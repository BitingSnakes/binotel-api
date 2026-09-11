"""High-level wrappers around Binotel API endpoints."""

from __future__ import annotations

from copy import copy
from math import isfinite
from typing import TYPE_CHECKING, Any, Self, TypeVar

from pydantic import BaseModel

from .exceptions import BinotelRequestError, BinotelValidationError
from .schemas import (
    CustomerData,
    LabelData,
    SettingsEmployeeData,
    SettingsRouteData,
    SettingsVoiceFileData,
    StatData,
)

if TYPE_CHECKING:
    from .client import BinotelClient

T = TypeVar("T", bound=BaseModel)


def _collection(model: type[T], value: Any) -> list[T]:
    if value is None or value == "":
        return []
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        raise BinotelRequestError("API returned a collection in an unexpected format")
    return [model.model_validate(item) for item in value]


def _require(params: dict[str, Any], key: str) -> Any:
    value = params.get(key)
    if value is None or value == "":
        raise BinotelValidationError(f"{key} is required")
    return value


def _string_size(params: dict[str, Any], key: str, size: int, *, required: bool) -> None:
    value = params.get(key)
    if value is None:
        if required:
            raise BinotelValidationError(f"{key} is required")
        return
    if not isinstance(value, str) or len(value) != size:
        raise BinotelValidationError(f"{key} must be a {size}-character string")


def _nonnegative_number(params: dict[str, Any], key: str) -> None:
    value = params.get(key)
    if value is None:
        return
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = -1
    if isinstance(value, bool) or not isfinite(number) or number < 0:
        raise BinotelValidationError(f"{key} must be a non-negative number")


def _numeric(params: dict[str, Any], key: str, *, required: bool = False) -> None:
    value = _require(params, key) if required else params.get(key)
    if value is None:
        return
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float("nan")
    if isinstance(value, bool) or not isfinite(number):
        raise BinotelValidationError(f"{key} must be numeric")


def _validate_customer(params: dict[str, Any]) -> None:
    name = _require(params, "name")
    if not isinstance(name, str):
        raise BinotelValidationError("name must be a string")
    numbers = params.get("numbers")
    if numbers is not None:
        if not isinstance(numbers, list):
            raise BinotelValidationError("numbers must be a list")
        for number in numbers:
            if not isinstance(number, str) or len(number) != 10:
                raise BinotelValidationError("each number must be a 10-character string")
    email = params.get("email")
    if email is not None and (not isinstance(email, str) or "@" not in email):
        raise BinotelValidationError("email must be a valid email address")
    description = params.get("description")
    if description is not None and not isinstance(description, str):
        raise BinotelValidationError("description must be a string")
    assigned = params.get("assignedToEmployee")
    if assigned is not None:
        if not isinstance(assigned, dict):
            raise BinotelValidationError("assignedToEmployee must be an object")
        _numeric(assigned, "internalNumber")
        _numeric(assigned, "id")
    labels = params.get("labels")
    if labels is not None:
        if not isinstance(labels, dict):
            raise BinotelValidationError("labels must be an object")
        _numeric(labels, "id")
        if labels.get("name") is not None and not isinstance(labels["name"], str):
            raise BinotelValidationError("labels.name must be a string")


class BaseResource:
    model: str

    def __init__(self, client: BinotelClient) -> None:
        self.client = client
        self._cache_seconds: int | None = None

    def cache(self, seconds: int = -1) -> Self:
        """Return a copy whose resource calls use the requested cache lifetime."""
        if seconds < -1:
            raise BinotelValidationError("cache seconds must be -1 or greater")
        resource = copy(self)
        resource._cache_seconds = seconds
        return resource

    def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        response_key: str | None = None,
        cache_seconds: int | None = None,
    ) -> Any:
        effective_cache = cache_seconds if self._cache_seconds is None else self._cache_seconds
        return self.client.request(
            f"{self.model}/{endpoint}",
            params,
            response_key=response_key,
            cache_seconds=effective_cache,
        )

    def _models(
        self,
        endpoint: str,
        model: type[T],
        params: dict[str, Any] | None = None,
        *,
        response_key: str | None = None,
        cache_seconds: int | None = None,
    ) -> list[T]:
        return _collection(
            model,
            self._request(
                endpoint,
                params,
                response_key=response_key,
                cache_seconds=cache_seconds,
            ),
        )


class Customers(BaseResource):
    model = "customers"

    def list(self) -> list[CustomerData]:
        return self._models("list", CustomerData)

    def take_by_id(self, customer_id: int | list[int]) -> list[CustomerData]:
        return self._models(
            "take-by-id",
            CustomerData,
            {"customerID": customer_id},
            response_key="customerData",
        )

    def take_by_label(self, label_id: int) -> list[CustomerData]:
        return self._models(
            "take-by-label",
            CustomerData,
            {"labelID": label_id},
            response_key="customerData",
        )

    def search(self, subject: str) -> list[CustomerData]:
        _require({"subject": subject}, "subject")
        return self._models(
            "search", CustomerData, {"subject": subject}, response_key="customerData"
        )

    def create(self, params: dict[str, Any]) -> int:
        _validate_customer(params)
        return int(self._request("create", params, response_key="customerID"))

    def update(self, params: dict[str, Any]) -> None:
        _validate_customer(params)
        self._request("update", params)

    def delete(self, customer_id: int) -> None:
        self._request("delete", {"customerID": customer_id})

    def list_of_labels(self) -> list[LabelData]:
        return self._models("listOfLabels", LabelData, response_key="listOfLabels")


class Stats(BaseResource):
    model = "stats"

    def _stats(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        cache_seconds: int | None = None,
    ) -> list[StatData]:
        return self._models(
            endpoint,
            StatData,
            params,
            response_key="callDetails",
            cache_seconds=cache_seconds,
        )

    def incoming_calls_for_period(self, start_time: int, stop_time: int) -> list[StatData]:
        return self._stats(
            "incoming-calls-for-period", {"startTime": start_time, "stopTime": stop_time}
        )

    def outgoing_calls_for_period(self, start_time: int, stop_time: int) -> list[StatData]:
        return self._stats(
            "outgoing-calls-for-period", {"startTime": start_time, "stopTime": stop_time}
        )

    def call_tracking_calls_for_period(self, start_time: int, stop_time: int) -> list[StatData]:
        return self._stats(
            "calltracking-calls-for-period", {"startTime": start_time, "stopTime": stop_time}
        )

    def all_incoming_calls_since(self, timestamp: int) -> list[StatData]:
        return self._stats("all-incoming-calls-since", {"timestamp": timestamp})

    def all_outgoing_calls_since(self, timestamp: int) -> list[StatData]:
        return self._stats("all-outgoing-calls-since", {"timestamp": timestamp})

    def list_of_calls_by_internal_number_for_period(
        self, internal_number: int, start_time: int, stop_time: int
    ) -> list[StatData]:
        return self._stats(
            "list-of-calls-by-internal-number-for-period",
            {
                "internalNumber": internal_number,
                "startTime": start_time,
                "stopTime": stop_time,
            },
        )

    def list_of_calls_per_day(self, day_in_timestamp: int | None = None) -> list[StatData]:
        return self._stats("list-of-calls-per-day", {"dayInTimestamp": day_in_timestamp})

    def list_of_calls_for_period(self, start_time: int, stop_time: int) -> list[StatData]:
        return self._stats(
            "list-of-calls-for-period", {"startTime": start_time, "stopTime": stop_time}
        )

    def list_of_lost_calls_for_today(self) -> list[StatData]:
        return self._stats("list-of-lost-calls-for-today", cache_seconds=10)

    def online_calls(self) -> list[StatData]:
        return self._stats("online-calls", cache_seconds=5)

    def history_by_external_number(self, external_numbers: list[str]) -> list[StatData]:
        return self._stats(
            "history-by-external-number",
            {"externalNumbers": external_numbers},
            cache_seconds=5,
        )

    def history_by_customer_id(self, customer_id: int | list[int]) -> list[StatData]:
        return self._stats("history-by-customer-id", {"customerID": customer_id}, cache_seconds=5)

    def recent_calls_by_internal_number(self, internal_number: int) -> list[StatData]:
        return self._stats(
            "recent-calls-by-internal-number",
            {"internalNumber": internal_number},
            cache_seconds=5,
        )

    def call_details(self, general_call_ids: list[int]) -> list[StatData]:
        return self._stats("call-details", {"generalCallID": general_call_ids}, cache_seconds=10)

    def call_record(self, general_call_id: int) -> str:
        value = self._request(
            "call-record",
            {"generalCallID": general_call_id},
            response_key="url",
            cache_seconds=10,
        )
        if isinstance(value, dict):
            value = value.get("url")
        if not isinstance(value, str):
            raise BinotelRequestError("API response did not contain a recording URL")
        return value


class Settings(BaseResource):
    model = "settings"

    def list_of_employees(self) -> list[SettingsEmployeeData]:
        return self._models(
            "list-of-employees",
            SettingsEmployeeData,
            response_key="listOfEmployees",
            cache_seconds=5,
        )

    def list_of_routes(self) -> dict[str, list[SettingsRouteData]]:
        data = self._request("list-of-routes", response_key="listOfRoutes", cache_seconds=5)
        if not isinstance(data, dict):
            raise BinotelRequestError("API returned routes in an unexpected format")
        return {key: _collection(SettingsRouteData, value) for key, value in data.items()}

    def list_of_voice_files(self) -> list[SettingsVoiceFileData]:
        result = self._request("list-of-voice-files", cache_seconds=5)
        if not isinstance(result, dict):
            raise BinotelRequestError("API returned voice files in an unexpected format")
        # Accept the legacy response key as a compatibility fallback.
        value = result.get("listOfVoiceFiles", result.get("listOfEmployees"))
        return _collection(SettingsVoiceFileData, value)


class Calls(BaseResource):
    model = "calls"

    def internal_number_to_external_number(self, params: dict[str, Any]) -> int | None:
        _numeric(params, "internalNumber", required=True)
        _string_size(params, "externalNumber", 10, required=True)
        _string_size(params, "pbxNumber", 10, required=False)
        _nonnegative_number(params, "limitCallTime")
        _nonnegative_number(params, "callTimeToExt")
        self._validate_flags(params, "playbackWaiting", "async")
        return self._optional_call_id(
            self._request(
                "internal-number-to-external-number", params, response_key="generalCallID"
            )
        )

    def external_number_to_external_number(self, params: dict[str, Any]) -> int | None:
        _string_size(params, "externalNumber1", 10, required=True)
        _string_size(params, "externalNumber2", 10, required=True)
        _string_size(params, "pbxNumber", 10, required=True)
        _nonnegative_number(params, "limitCallTime")
        self._validate_flags(params, "playbackWaiting")
        return self._optional_call_id(
            self._request(
                "external-number-to-external-number", params, response_key="generalCallID"
            )
        )

    def external_number_to_incoming_call(self, params: dict[str, Any]) -> int | None:
        _string_size(params, "externalNumber", 10, required=True)
        _string_size(params, "pbxNumber", 10, required=True)
        _string_size(params, "pbxNumberInExternalCall", 10, required=False)
        return self._optional_call_id(
            self._request("external-number-to-incoming-call", params, response_key="generalCallID")
        )

    def attended_call_transfer(self, general_call_id: int, external_number: str) -> None:
        params = {"generalCallID": general_call_id, "externalNumber": external_number}
        _string_size(params, "externalNumber", 10, required=True)
        self._request("attended-call-transfer", params)

    def hangup_call(self, general_call_id: int) -> None:
        self._request("hangup-call", {"generalCallID": general_call_id})

    def call_with_announcement(self, params: dict[str, Any]) -> None:
        _string_size(params, "externalNumber", 10, required=True)
        _numeric(params, "voiceFileID", required=True)
        self._request("call-with-announcement", params)

    def call_with_interactive_voice_response(self, params: dict[str, Any]) -> None:
        _string_size(params, "externalNumber", 10, required=True)
        ivr_name = _require(params, "ivrName")
        if not isinstance(ivr_name, str):
            raise BinotelValidationError("ivrName must be a string")
        self._request("call-with-interactive-voice-response", params)

    @staticmethod
    def _validate_flags(params: dict[str, Any], *keys: str) -> None:
        for key in keys:
            if params.get(key) is not None and params[key] not in {"TRUE", "FALSE"}:
                raise BinotelValidationError(f"{key} must be TRUE or FALSE")

    @staticmethod
    def _optional_call_id(value: Any) -> int | None:
        return None if value is None else int(value)
