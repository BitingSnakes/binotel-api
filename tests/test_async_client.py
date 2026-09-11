from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from whenever import ZonedDateTime

from binotel_api import AsyncBinotelClient, BinotelConfig
from binotel_api.async_resources import AsyncCalls, AsyncCustomers, AsyncSettings, AsyncStats
from binotel_api.resources import Calls, Customers, Settings, Stats


class FakeAsyncResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload

    async def json(self) -> Any:
        return self.payload


class FakeAsyncHttpClient:
    def __init__(self, handler: Callable[[str, dict[str, Any]], FakeAsyncResponse]) -> None:
        self.handler = handler
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> FakeAsyncResponse:
        return self.handler(url, kwargs["json"])

    def close(self) -> None:
        self.closed = True

    def __bool__(self) -> bool:
        return False


def config(**overrides: object) -> BinotelConfig:
    values = {
        "key": "api-key",
        "secret": "api-secret",
        "throttle_ms": 0,
        "retry_times": 1,
    }
    values.update(overrides)
    return BinotelConfig(**values)


def test_async_client_posts_credentials_and_closes() -> None:
    def handler(url: str, payload: dict[str, Any]) -> FakeAsyncResponse:
        assert url == "https://api.binotel.com/api/4.0/customers/list.json"
        assert payload == {"key": "api-key", "secret": "api-secret"}
        return FakeAsyncResponse(200, [{"id": "7", "name": "Acme"}])

    fake = FakeAsyncHttpClient(handler)

    async def scenario() -> None:
        async with AsyncBinotelClient(config(), http_client=fake) as client:
            customers = await client.customers.list()
            assert customers[0].id == 7
            assert customers[0].name == "Acme"

    asyncio.run(scenario())
    assert fake.closed


def test_async_cache_coalesces_concurrent_requests() -> None:
    requests = 0

    def handler(url: str, payload: dict[str, Any]) -> FakeAsyncResponse:
        nonlocal requests
        requests += 1
        return FakeAsyncResponse(200, {"callDetails": []})

    async def scenario() -> None:
        async with AsyncBinotelClient(config(), http_client=FakeAsyncHttpClient(handler)) as client:
            first, second = await asyncio.gather(
                client.stats.online_calls(), client.stats.online_calls()
            )
            assert first == second == []

    asyncio.run(scenario())
    assert requests == 1


def test_async_cache_does_not_serialize_different_keys() -> None:
    class ConcurrentHttpClient:
        def __init__(self) -> None:
            self.started = 0
            self.both_started = asyncio.Event()

        async def post(self, url: str, **kwargs: Any) -> FakeAsyncResponse:
            self.started += 1
            if self.started == 2:
                self.both_started.set()
            await self.both_started.wait()
            return FakeAsyncResponse(200, {})

        def close(self) -> None:
            pass

    async def scenario() -> None:
        transport = ConcurrentHttpClient()
        async with AsyncBinotelClient(config(), http_client=transport) as client:
            results = await asyncio.wait_for(
                asyncio.gather(
                    client.request("first", cache_seconds=10),
                    client.request("second", cache_seconds=10),
                ),
                timeout=1,
            )
        assert results == [{}, {}]

    asyncio.run(scenario())


def test_async_custom_cache_duration_overrides_endpoint_default() -> None:
    requests = 0

    def handler(url: str, payload: dict[str, Any]) -> FakeAsyncResponse:
        nonlocal requests
        requests += 1
        return FakeAsyncResponse(200, {"callDetails": []})

    async def scenario() -> None:
        async with AsyncBinotelClient(config(), http_client=FakeAsyncHttpClient(handler)) as client:
            stats = client.stats.cache(0)
            assert await stats.online_calls() == []
            assert await stats.online_calls() == []

    asyncio.run(scenario())
    assert requests == 2


def test_async_throttle_spaces_concurrent_requests() -> None:
    request_times: list[float] = []

    def handler(url: str, payload: dict[str, Any]) -> FakeAsyncResponse:
        request_times.append(time.monotonic())
        return FakeAsyncResponse(200, {})

    async def scenario() -> None:
        async with AsyncBinotelClient(
            config(throttle_ms=50), http_client=FakeAsyncHttpClient(handler)
        ) as client:
            await asyncio.gather(client.request("first"), client.request("second"))

    asyncio.run(scenario())
    assert request_times[1] - request_times[0] >= 0.04


def test_async_stats_accept_whenever_times() -> None:
    payloads: list[dict[str, Any]] = []

    def handler(url: str, payload: dict[str, Any]) -> FakeAsyncResponse:
        payloads.append(payload)
        return FakeAsyncResponse(200, {"callDetails": []})

    async def scenario() -> None:
        start = ZonedDateTime(2024, 9, 9, tz="Europe/Kyiv")
        stop = start.add(days=1)
        async with AsyncBinotelClient(config(), http_client=FakeAsyncHttpClient(handler)) as client:
            await client.stats.incoming_calls_for_period(start, stop)

        assert payloads == [
            {
                "startTime": start.timestamp(),
                "stopTime": stop.timestamp(),
                "key": "api-key",
                "secret": "api-secret",
            }
        ]

    asyncio.run(scenario())


def test_async_resources_match_sync_public_api() -> None:
    pairs = [
        (Customers, AsyncCustomers),
        (Stats, AsyncStats),
        (Settings, AsyncSettings),
        (Calls, AsyncCalls),
    ]
    for sync_class, async_class in pairs:
        sync_methods = {
            name
            for name, value in inspect.getmembers(sync_class, inspect.isfunction)
            if not name.startswith("_")
        }
        async_methods = {
            name
            for name, value in inspect.getmembers(async_class, inspect.isfunction)
            if not name.startswith("_")
        }
        assert async_methods == sync_methods
        assert all(
            name == "cache" or inspect.iscoroutinefunction(getattr(async_class, name))
            for name in async_methods
        )


def test_every_async_resource_method_maps_to_an_api_endpoint() -> None:
    paths: list[str] = []

    def handler(url: str, payload: dict[str, Any]) -> FakeAsyncResponse:
        path = urlparse(url).path
        paths.append(path.removeprefix("/api/4.0/").removesuffix(".json"))
        if path.endswith("customers/list.json"):
            return FakeAsyncResponse(200, [])
        if "customers" in path:
            return FakeAsyncResponse(
                200,
                {"customerData": [], "customerID": 1, "listOfLabels": []},
            )
        if "stats/call-record" in path:
            return FakeAsyncResponse(200, {"url": {"url": "https://example.test/call.mp3"}})
        if "stats" in path:
            return FakeAsyncResponse(200, {"callDetails": []})
        if "settings/list-of-routes" in path:
            return FakeAsyncResponse(200, {"listOfRoutes": {}})
        if "settings/list-of-voice-files" in path:
            return FakeAsyncResponse(200, {"listOfVoiceFiles": []})
        if "settings" in path:
            return FakeAsyncResponse(200, {"listOfEmployees": []})
        return FakeAsyncResponse(200, {"generalCallID": 1})

    async def scenario() -> None:
        async with AsyncBinotelClient(config(), http_client=FakeAsyncHttpClient(handler)) as client:
            await client.customers.list()
            await client.customers.take_by_id(1)
            await client.customers.take_by_label(1)
            await client.customers.search("Acme")
            await client.customers.create({"name": "Acme"})
            await client.customers.update({"name": "Acme"})
            await client.customers.delete(1)
            await client.customers.list_of_labels()

            await client.stats.incoming_calls_for_period(1, 2)
            await client.stats.outgoing_calls_for_period(1, 2)
            await client.stats.call_tracking_calls_for_period(1, 2)
            await client.stats.all_incoming_calls_since(1)
            await client.stats.all_outgoing_calls_since(1)
            await client.stats.list_of_calls_by_internal_number_for_period(801, 1, 2)
            await client.stats.list_of_calls_per_day()
            await client.stats.list_of_calls_for_period(1, 2)
            await client.stats.list_of_lost_calls_for_today()
            await client.stats.online_calls()
            await client.stats.history_by_external_number(["0671234567"])
            await client.stats.history_by_customer_id(1)
            await client.stats.recent_calls_by_internal_number(801)
            await client.stats.call_details([1])
            assert await client.stats.call_record(1) == "https://example.test/call.mp3"

            await client.settings.list_of_employees()
            await client.settings.list_of_routes()
            await client.settings.list_of_voice_files()

            await client.calls.internal_number_to_external_number(
                {"internalNumber": 801, "externalNumber": "0671234567"}
            )
            await client.calls.external_number_to_external_number(
                {
                    "externalNumber1": "0671234567",
                    "externalNumber2": "0501234567",
                    "pbxNumber": "0441234567",
                }
            )
            await client.calls.external_number_to_incoming_call(
                {"externalNumber": "0671234567", "pbxNumber": "0441234567"}
            )
            await client.calls.attended_call_transfer(1, "0671234567")
            await client.calls.hangup_call(1)
            await client.calls.call_with_announcement(
                {"externalNumber": "0671234567", "voiceFileID": 1}
            )
            await client.calls.call_with_interactive_voice_response(
                {"externalNumber": "0671234567", "ivrName": "menu"}
            )

    asyncio.run(scenario())
    assert paths == [
        "customers/list",
        "customers/take-by-id",
        "customers/take-by-label",
        "customers/search",
        "customers/create",
        "customers/update",
        "customers/delete",
        "customers/listOfLabels",
        "stats/incoming-calls-for-period",
        "stats/outgoing-calls-for-period",
        "stats/calltracking-calls-for-period",
        "stats/all-incoming-calls-since",
        "stats/all-outgoing-calls-since",
        "stats/list-of-calls-by-internal-number-for-period",
        "stats/list-of-calls-per-day",
        "stats/list-of-calls-for-period",
        "stats/list-of-lost-calls-for-today",
        "stats/online-calls",
        "stats/history-by-external-number",
        "stats/history-by-customer-id",
        "stats/recent-calls-by-internal-number",
        "stats/call-details",
        "stats/call-record",
        "settings/list-of-employees",
        "settings/list-of-routes",
        "settings/list-of-voice-files",
        "calls/internal-number-to-external-number",
        "calls/external-number-to-external-number",
        "calls/external-number-to-incoming-call",
        "calls/attended-call-transfer",
        "calls/hangup-call",
        "calls/call-with-announcement",
        "calls/call-with-interactive-voice-response",
    ]
