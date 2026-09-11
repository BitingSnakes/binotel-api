from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import pytest

from binotel_api import BinotelClient, BinotelConfig, BinotelRequestError
from binotel_api.exceptions import BinotelValidationError


class FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload

    def json(self) -> Any:
        return self.payload


class FakeHttpClient:
    def __init__(self, handler: Callable[[str, dict[str, Any]], FakeResponse]) -> None:
        self.handler = handler
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.handler(url, kwargs["json"])

    def close(self) -> None:
        self.closed = True


def config(**overrides: object) -> BinotelConfig:
    values = {
        "key": "api-key",
        "secret": "api-secret",
        "throttle_ms": 0,
        "retry_times": 1,
    }
    values.update(overrides)
    return BinotelConfig(**values)


def test_customer_list_posts_credentials_and_parses_models() -> None:
    def handler(url: str, payload: dict[str, Any]) -> FakeResponse:
        assert url == "https://api.binotel.com/api/4.0/customers/list.json"
        assert payload == {"key": "api-key", "secret": "api-secret"}
        return FakeResponse(200, [{"id": "7", "name": "Acme"}])

    with BinotelClient(config(), http_client=FakeHttpClient(handler)) as client:
        customers = client.customers.list()

    assert len(customers) == 1
    assert customers[0].id == 7
    assert customers[0].name == "Acme"


def test_cached_resource_makes_one_http_request() -> None:
    requests = 0

    def handler(url: str, payload: dict[str, Any]) -> FakeResponse:
        nonlocal requests
        requests += 1
        return FakeResponse(200, {"callDetails": []})

    with BinotelClient(config(), http_client=FakeHttpClient(handler)) as client:
        assert client.stats.online_calls() == []
        assert client.stats.online_calls() == []

    assert requests == 1


def test_custom_resource_cache_duration_is_chainable() -> None:
    requests = 0

    def handler(url: str, payload: dict[str, Any]) -> FakeResponse:
        nonlocal requests
        requests += 1
        return FakeResponse(200, [])

    with BinotelClient(config(), http_client=FakeHttpClient(handler)) as client:
        customers = client.customers.cache()
        assert customers.list() == []
        assert customers.list() == []

    assert requests == 1


def test_validation_happens_before_request() -> None:
    fake = FakeHttpClient(lambda _url, _payload: FakeResponse(200, {}))
    with BinotelClient(config(), http_client=fake) as client:
        with pytest.raises(BinotelValidationError, match="10-character"):
            client.calls.internal_number_to_external_number(
                {"internalNumber": 801, "externalNumber": "123"}
            )


def test_hangup_uses_correct_endpoint() -> None:
    paths: list[str] = []

    def handler(url: str, payload: dict[str, Any]) -> FakeResponse:
        paths.append(urlparse(url).path)
        return FakeResponse(200, {})

    with BinotelClient(config(), http_client=FakeHttpClient(handler)) as client:
        client.calls.hangup_call(42)

    assert paths == ["/api/4.0/calls/hangup-call.json"]


def test_non_retryable_http_error_is_wrapped() -> None:
    fake = FakeHttpClient(lambda _url, _payload: FakeResponse(400, {"error": "bad"}))
    with BinotelClient(config(), http_client=fake) as client:
        with pytest.raises(BinotelRequestError, match="HTTP status 400"):
            client.customers.list()


def test_environment_defaults_can_be_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BINOTEL_API_URL", raising=False)
    monkeypatch.setenv("BINOTEL_API_KEY", "from-env")

    loaded = BinotelConfig.from_env()

    assert loaded.url == "https://api.binotel.com/api/"
    assert loaded.key == "from-env"


def test_retries_transient_status() -> None:
    requests = 0

    def handler(url: str, payload: dict[str, Any]) -> FakeResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            return FakeResponse(503, {"error": "temporary"})
        return FakeResponse(200, [])

    retrying = config(retry_times=2, retry_sleep_ms=0, retry_max_sleep_ms=0)
    with BinotelClient(retrying, http_client=FakeHttpClient(handler)) as client:
        assert client.customers.list() == []

    assert requests == 2


def test_every_resource_method_maps_to_an_api_endpoint() -> None:
    paths: list[str] = []

    def handler(url: str, payload: dict[str, Any]) -> FakeResponse:
        path = urlparse(url).path
        paths.append(path.removeprefix("/api/4.0/").removesuffix(".json"))
        if path.endswith("customers/list.json"):
            return FakeResponse(200, [])
        if "customers" in path:
            return FakeResponse(
                200,
                {"customerData": [], "customerID": 1, "listOfLabels": []},
            )
        if "stats/call-record" in path:
            return FakeResponse(200, {"url": {"url": "https://example.test/call.mp3"}})
        if "stats" in path:
            return FakeResponse(200, {"callDetails": []})
        if "settings/list-of-routes" in path:
            return FakeResponse(200, {"listOfRoutes": {}})
        if "settings/list-of-voice-files" in path:
            return FakeResponse(200, {"listOfVoiceFiles": []})
        if "settings" in path:
            return FakeResponse(200, {"listOfEmployees": []})
        return FakeResponse(200, {"generalCallID": 1})

    with BinotelClient(config(), http_client=FakeHttpClient(handler)) as client:
        client.customers.list()
        client.customers.take_by_id(1)
        client.customers.take_by_label(1)
        client.customers.search("Acme")
        client.customers.create({"name": "Acme"})
        client.customers.update({"name": "Acme"})
        client.customers.delete(1)
        client.customers.list_of_labels()

        client.stats.incoming_calls_for_period(1, 2)
        client.stats.outgoing_calls_for_period(1, 2)
        client.stats.call_tracking_calls_for_period(1, 2)
        client.stats.all_incoming_calls_since(1)
        client.stats.all_outgoing_calls_since(1)
        client.stats.list_of_calls_by_internal_number_for_period(801, 1, 2)
        client.stats.list_of_calls_per_day()
        client.stats.list_of_calls_for_period(1, 2)
        client.stats.list_of_lost_calls_for_today()
        client.stats.online_calls()
        client.stats.history_by_external_number(["0671234567"])
        client.stats.history_by_customer_id(1)
        client.stats.recent_calls_by_internal_number(801)
        client.stats.call_details([1])
        assert client.stats.call_record(1) == "https://example.test/call.mp3"

        client.settings.list_of_employees()
        client.settings.list_of_routes()
        client.settings.list_of_voice_files()

        client.calls.internal_number_to_external_number(
            {"internalNumber": 801, "externalNumber": "0671234567"}
        )
        client.calls.external_number_to_external_number(
            {
                "externalNumber1": "0671234567",
                "externalNumber2": "0501234567",
                "pbxNumber": "0441234567",
            }
        )
        client.calls.external_number_to_incoming_call(
            {"externalNumber": "0671234567", "pbxNumber": "0441234567"}
        )
        client.calls.attended_call_transfer(1, "0671234567")
        client.calls.hangup_call(1)
        client.calls.call_with_announcement({"externalNumber": "0671234567", "voiceFileID": 1})
        client.calls.call_with_interactive_voice_response(
            {"externalNumber": "0671234567", "ivrName": "menu"}
        )

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
