"""Async wrappers around Binotel API endpoints."""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING, Any, Self, TypeVar

from pydantic import BaseModel

from .exceptions import BinotelRequestError, BinotelValidationError
from .resources import (
    _collection,
    _nonnegative_number,
    _numeric,
    _require,
    _string_size,
    _validate_customer,
)
from .schemas import (
    CustomerData,
    LabelData,
    SettingsEmployeeData,
    SettingsRouteData,
    SettingsVoiceFileData,
    StatData,
)
from .timestamps import TimestampLike, _period, _timestamp

if TYPE_CHECKING:
    from .async_client import AsyncBinotelClient

T = TypeVar("T", bound=BaseModel)


class AsyncBaseResource:
    """Provide shared request and cache behavior for asynchronous resources."""

    model: str

    def __init__(self, client: AsyncBinotelClient) -> None:
        self.client = client
        self._cache_seconds: int | None = None

    def cache(self, seconds: int = -1) -> Self:
        """Return a copy whose resource calls use the requested cache lifetime."""
        if seconds < -1:
            raise BinotelValidationError("cache seconds must be -1 or greater")
        resource = copy(self)
        resource._cache_seconds = seconds
        return resource

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        response_key: str | None = None,
        cache_seconds: int | None = None,
    ) -> Any:
        effective_cache = cache_seconds if self._cache_seconds is None else self._cache_seconds
        return await self.client.request(
            f"{self.model}/{endpoint}",
            params,
            response_key=response_key,
            cache_seconds=effective_cache,
        )

    async def _models(
        self,
        endpoint: str,
        model: type[T],
        params: dict[str, Any] | None = None,
        *,
        response_key: str | None = None,
        cache_seconds: int | None = None,
    ) -> list[T]:
        value = await self._request(
            endpoint,
            params,
            response_key=response_key,
            cache_seconds=cache_seconds,
        )
        return _collection(model, value)


class AsyncCustomers(AsyncBaseResource):
    """Provide asynchronous access to customer endpoints."""

    model = "customers"

    async def list(self) -> list[CustomerData]:
        """Return all customers."""
        return await self._models("list", CustomerData)

    async def take_by_id(self, customer_id: int | list[int]) -> list[CustomerData]:
        """Return customers matching one or more customer IDs."""
        return await self._models(
            "take-by-id",
            CustomerData,
            {"customerID": customer_id},
            response_key="customerData",
        )

    async def take_by_label(self, label_id: int) -> list[CustomerData]:
        """Return customers assigned to a label."""
        return await self._models(
            "take-by-label",
            CustomerData,
            {"labelID": label_id},
            response_key="customerData",
        )

    async def search(self, subject: str) -> list[CustomerData]:
        """Search customers by a Binotel-supported subject."""
        _require({"subject": subject}, "subject")
        return await self._models(
            "search", CustomerData, {"subject": subject}, response_key="customerData"
        )

    async def create(self, params: dict[str, Any]) -> int:
        """Create a customer and return its ID."""
        _validate_customer(params)
        return int(await self._request("create", params, response_key="customerID"))

    async def update(self, params: dict[str, Any]) -> None:
        """Update a customer from the supplied API fields."""
        _validate_customer(params)
        await self._request("update", params)

    async def delete(self, customer_id: int) -> None:
        """Delete a customer by ID."""
        await self._request("delete", {"customerID": customer_id})

    async def list_of_labels(self) -> list[LabelData]:
        """Return all available customer labels."""
        return await self._models("listOfLabels", LabelData, response_key="listOfLabels")


class AsyncStats(AsyncBaseResource):
    """Provide asynchronous access to call statistics endpoints."""

    model = "stats"

    async def _stats(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        cache_seconds: int | None = None,
    ) -> list[StatData]:
        return await self._models(
            endpoint,
            StatData,
            params,
            response_key="callDetails",
            cache_seconds=cache_seconds,
        )

    async def incoming_calls_for_period(
        self, start_time: TimestampLike, stop_time: TimestampLike
    ) -> list[StatData]:
        """Return incoming calls within an inclusive time period."""
        return await self._stats("incoming-calls-for-period", _period(start_time, stop_time))

    async def outgoing_calls_for_period(
        self, start_time: TimestampLike, stop_time: TimestampLike
    ) -> list[StatData]:
        """Return outgoing calls within an inclusive time period."""
        return await self._stats("outgoing-calls-for-period", _period(start_time, stop_time))

    async def call_tracking_calls_for_period(
        self, start_time: TimestampLike, stop_time: TimestampLike
    ) -> list[StatData]:
        """Return call-tracking calls within an inclusive time period."""
        return await self._stats("calltracking-calls-for-period", _period(start_time, stop_time))

    async def all_incoming_calls_since(self, timestamp: TimestampLike) -> list[StatData]:
        """Return incoming calls recorded since an exact time."""
        return await self._stats(
            "all-incoming-calls-since", {"timestamp": _timestamp(timestamp, "timestamp")}
        )

    async def all_outgoing_calls_since(self, timestamp: TimestampLike) -> list[StatData]:
        """Return outgoing calls recorded since an exact time."""
        return await self._stats(
            "all-outgoing-calls-since", {"timestamp": _timestamp(timestamp, "timestamp")}
        )

    async def list_of_calls_by_internal_number_for_period(
        self,
        internal_number: int,
        start_time: TimestampLike,
        stop_time: TimestampLike,
    ) -> list[StatData]:
        """Return calls for an internal number within a time period."""
        return await self._stats(
            "list-of-calls-by-internal-number-for-period",
            {"internalNumber": internal_number, **_period(start_time, stop_time)},
        )

    async def list_of_calls_per_day(
        self, day_in_timestamp: TimestampLike | None = None
    ) -> list[StatData]:
        """Return calls for the day containing the supplied exact time."""
        params = (
            {}
            if day_in_timestamp is None
            else {"dayInTimestamp": _timestamp(day_in_timestamp, "day_in_timestamp")}
        )
        return await self._stats("list-of-calls-per-day", params)

    async def list_of_calls_for_period(
        self, start_time: TimestampLike, stop_time: TimestampLike
    ) -> list[StatData]:
        """Return all calls within an inclusive time period."""
        return await self._stats("list-of-calls-for-period", _period(start_time, stop_time))

    async def list_of_lost_calls_for_today(self) -> list[StatData]:
        """Return calls lost during the current day."""
        return await self._stats("list-of-lost-calls-for-today", cache_seconds=10)

    async def online_calls(self) -> list[StatData]:
        """Return calls that are currently active."""
        return await self._stats("online-calls", cache_seconds=5)

    async def history_by_external_number(self, external_numbers: list[str]) -> list[StatData]:
        """Return call history for one or more external numbers."""
        return await self._stats(
            "history-by-external-number",
            {"externalNumbers": external_numbers},
            cache_seconds=5,
        )

    async def history_by_customer_id(self, customer_id: int | list[int]) -> list[StatData]:
        """Return call history for one or more customer IDs."""
        return await self._stats(
            "history-by-customer-id", {"customerID": customer_id}, cache_seconds=5
        )

    async def recent_calls_by_internal_number(self, internal_number: int) -> list[StatData]:
        """Return recent calls for an employee's internal number."""
        return await self._stats(
            "recent-calls-by-internal-number",
            {"internalNumber": internal_number},
            cache_seconds=5,
        )

    async def call_details(self, general_call_ids: list[int]) -> list[StatData]:
        """Return detailed statistics for one or more general call IDs."""
        return await self._stats(
            "call-details", {"generalCallID": general_call_ids}, cache_seconds=10
        )

    async def call_record(self, general_call_id: int) -> str:
        """Return the temporary recording URL for a general call ID."""
        value = await self._request(
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


class AsyncSettings(AsyncBaseResource):
    """Provide asynchronous access to PBX settings endpoints."""

    model = "settings"

    async def list_of_employees(self) -> list[SettingsEmployeeData]:
        """Return configured PBX employees."""
        return await self._models(
            "list-of-employees",
            SettingsEmployeeData,
            response_key="listOfEmployees",
            cache_seconds=5,
        )

    async def list_of_routes(self) -> dict[str, list[SettingsRouteData]]:
        """Return inbound routes grouped by the API response key."""
        data = await self._request("list-of-routes", response_key="listOfRoutes", cache_seconds=5)
        if not isinstance(data, dict):
            raise BinotelRequestError("API returned routes in an unexpected format")
        return {key: _collection(SettingsRouteData, value) for key, value in data.items()}

    async def list_of_voice_files(self) -> list[SettingsVoiceFileData]:
        """Return voice files available to call scenarios."""
        result = await self._request("list-of-voice-files", cache_seconds=5)
        if not isinstance(result, dict):
            raise BinotelRequestError("API returned voice files in an unexpected format")
        value = result.get("listOfVoiceFiles", result.get("listOfEmployees"))
        return _collection(SettingsVoiceFileData, value)


class AsyncCalls(AsyncBaseResource):
    """Provide asynchronous access to call-control endpoints."""

    model = "calls"

    async def internal_number_to_external_number(self, params: dict[str, Any]) -> int | None:
        """Connect an internal number to an external number."""
        _numeric(params, "internalNumber", required=True)
        _string_size(params, "externalNumber", 10, required=True)
        _string_size(params, "pbxNumber", 10, required=False)
        _nonnegative_number(params, "limitCallTime")
        _nonnegative_number(params, "callTimeToExt")
        self._validate_flags(params, "playbackWaiting", "async")
        return self._optional_call_id(
            await self._request(
                "internal-number-to-external-number", params, response_key="generalCallID"
            )
        )

    async def external_number_to_external_number(self, params: dict[str, Any]) -> int | None:
        """Connect two external numbers through the PBX."""
        _string_size(params, "externalNumber1", 10, required=True)
        _string_size(params, "externalNumber2", 10, required=True)
        _string_size(params, "pbxNumber", 10, required=True)
        _nonnegative_number(params, "limitCallTime")
        self._validate_flags(params, "playbackWaiting")
        return self._optional_call_id(
            await self._request(
                "external-number-to-external-number", params, response_key="generalCallID"
            )
        )

    async def external_number_to_incoming_call(self, params: dict[str, Any]) -> int | None:
        """Connect an external number to an incoming-call scenario."""
        _string_size(params, "externalNumber", 10, required=True)
        _string_size(params, "pbxNumber", 10, required=True)
        _string_size(params, "pbxNumberInExternalCall", 10, required=False)
        return self._optional_call_id(
            await self._request(
                "external-number-to-incoming-call", params, response_key="generalCallID"
            )
        )

    async def attended_call_transfer(self, general_call_id: int, external_number: str) -> None:
        """Transfer an active call to an external number with consultation."""
        params = {"generalCallID": general_call_id, "externalNumber": external_number}
        _string_size(params, "externalNumber", 10, required=True)
        await self._request("attended-call-transfer", params)

    async def hangup_call(self, general_call_id: int) -> None:
        """Terminate an active call."""
        await self._request("hangup-call", {"generalCallID": general_call_id})

    async def call_with_announcement(self, params: dict[str, Any]) -> None:
        """Place a call that plays a configured voice announcement."""
        _string_size(params, "externalNumber", 10, required=True)
        _numeric(params, "voiceFileID", required=True)
        await self._request("call-with-announcement", params)

    async def call_with_interactive_voice_response(self, params: dict[str, Any]) -> None:
        """Place a call using an interactive voice-response scenario."""
        _string_size(params, "externalNumber", 10, required=True)
        ivr_name = _require(params, "ivrName")
        if not isinstance(ivr_name, str):
            raise BinotelValidationError("ivrName must be a string")
        await self._request("call-with-interactive-voice-response", params)

    @staticmethod
    def _validate_flags(params: dict[str, Any], *keys: str) -> None:
        for key in keys:
            if params.get(key) is not None and params[key] not in {"TRUE", "FALSE"}:
                raise BinotelValidationError(f"{key} must be TRUE or FALSE")

    @staticmethod
    def _optional_call_id(value: Any) -> int | None:
        return None if value is None else int(value)
