"""Asynchronous Binotel HTTP client backed by wreq."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus
from typing import Any, Protocol, Self, TypeVar

import wreq

from .async_resources import AsyncCalls, AsyncCustomers, AsyncSettings, AsyncStats
from .config import BinotelConfig
from .exceptions import BinotelRequestError

T = TypeVar("T")


class AsyncHttpResponse(Protocol):
    """Describe the asynchronous response interface required by the client."""

    status: Any

    async def json(self) -> Any:
        """Decode and return the JSON response body."""
        ...


class AsyncHttpClient(Protocol):
    """Describe an injectable asynchronous HTTP transport."""

    async def post(self, url: str, **kwargs: Any) -> AsyncHttpResponse:
        """Send a POST request and return its response."""
        ...

    def close(self) -> None:
        """Release transport resources."""
        ...


_RETRYABLE_WREQ_ERRORS = (
    wreq.ConnectionError,
    wreq.ConnectionResetError,
    wreq.ProxyConnectionError,
    wreq.RequestError,
    wreq.TimeoutError,
    wreq.TlsError,
)

_NON_RETRYABLE_WREQ_ERRORS = (
    wreq.BodyError,
    wreq.BuilderError,
    wreq.RedirectError,
    wreq.StatusError,
    wreq.UpgradeError,
)


@dataclass(slots=True)
class _CacheEntry:
    value: Any
    expires_at: float | None


class _AsyncMemoryCache:
    def __init__(self) -> None:
        self._items: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._key_locks: dict[str, asyncio.Lock] = {}

    async def get_or_set(
        self,
        key: str,
        ttl: int,
        factory: Callable[[], Awaitable[T]],
    ) -> T:
        async with self._lock:
            if (entry := self._get(key)) is not None:
                return entry.value
            key_lock = self._key_locks.setdefault(key, asyncio.Lock())

        async with key_lock:
            async with self._lock:
                if (entry := self._get(key)) is not None:
                    return entry.value

            value = await factory()
            expires_at = None if ttl == -1 else time.monotonic() + ttl
            async with self._lock:
                self._items[key] = _CacheEntry(value, expires_at)
            return value

    def _get(self, key: str) -> _CacheEntry | None:
        entry = self._items.get(key)
        if entry is not None and (entry.expires_at is None or entry.expires_at > time.monotonic()):
            return entry
        self._items.pop(key, None)
        return None

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()


class AsyncBinotelClient:
    """Provide asynchronous access to Binotel customers, stats, settings, and calls."""

    def __init__(
        self,
        config: BinotelConfig | None = None,
        *,
        http_client: AsyncHttpClient | None = None,
    ) -> None:
        self.config = config if config is not None else BinotelConfig.from_env()
        self._base_url = self.config.url.rstrip("/")
        self._http: AsyncHttpClient = (
            http_client
            if http_client is not None
            else wreq.Client(
                timeout=timedelta(seconds=self.config.timeout),
                connect_timeout=timedelta(seconds=self.config.connect_timeout),
                headers={"Accept": "application/json"},
            )
        )
        self._cache = _AsyncMemoryCache()
        self._throttle_lock = asyncio.Lock()
        self._next_request_at = 0.0

        self.customers = AsyncCustomers(self)
        self.stats = AsyncStats(self)
        self.settings = AsyncSettings(self)
        self.calls = AsyncCalls(self)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        response_key: str | None = None,
        cache_seconds: int | None = None,
    ) -> Any:
        """Send a request to a Binotel API method and optionally cache its result."""
        params = dict(params or {})
        cache_key = self._cache_key(method, params)

        async def make_request() -> Any:
            result = await self._post(method, params)
            if response_key is None:
                return result
            if not isinstance(result, dict):
                return None
            return result.get(response_key)

        if cache_seconds is not None:
            return await self._cache.get_or_set(cache_key, cache_seconds, make_request)
        return await make_request()

    async def _post(self, method: str, params: dict[str, Any]) -> Any:
        payload = {**params, "key": self.config.key, "secret": self.config.secret}
        url = f"{self._base_url}/{self.config.version}/{method}.{self.config.response_format}"
        attempts = max(1, self.config.retry_times)

        for attempt in range(1, attempts + 1):
            await self._throttle()
            try:
                response = await self._http.post(url, json=payload)
                status = self._status_code(response)
                if (
                    status == HTTPStatus.TOO_MANY_REQUESTS
                    or status >= HTTPStatus.INTERNAL_SERVER_ERROR
                ) and attempt < attempts:
                    await self._retry_sleep(attempt)
                    continue
                if status >= HTTPStatus.BAD_REQUEST:
                    raise BinotelRequestError(f"API request failed with HTTP status {status}")
                return await response.json()
            except _RETRYABLE_WREQ_ERRORS as exc:
                if attempt < attempts:
                    await self._retry_sleep(attempt)
                    continue
                raise BinotelRequestError(f"API request failed: {exc}") from exc
            except wreq.DecodingError as exc:
                raise BinotelRequestError("API returned invalid JSON") from exc
            except _NON_RETRYABLE_WREQ_ERRORS as exc:
                raise BinotelRequestError(f"API request failed: {exc}") from exc
            except (ValueError, TypeError) as exc:
                raise BinotelRequestError("API returned invalid JSON") from exc
        raise BinotelRequestError("API request failed after all retries")

    async def _throttle(self) -> None:
        gap = self.config.throttle_ms / 1_000
        if gap <= 0:
            return
        async with self._throttle_lock:
            remaining = self._next_request_at - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._next_request_at = time.monotonic() + gap

    async def _retry_sleep(self, attempt: int) -> None:
        base = min(
            self.config.retry_sleep_ms * (2 ** (attempt - 1)),
            self.config.retry_max_sleep_ms,
        )
        jitter = random.randint(0, max(1, int(base * 0.1)))
        await asyncio.sleep((base + jitter) / 1_000)

    @staticmethod
    def _cache_key(method: str, params: dict[str, Any]) -> str:
        return method + ":" + json.dumps(params, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _status_code(response: AsyncHttpResponse) -> int:
        status = response.status
        if isinstance(status, int):
            return status
        return status.as_int()

    async def clear_cache(self) -> None:
        """Remove every response stored in the in-memory cache."""
        await self._cache.clear()

    def close(self) -> None:
        """Release resources owned by the HTTP transport."""
        self._http.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()


class AsyncBinotel(AsyncBinotelClient):
    """Short async compatibility name matching :class:`binotel_api.Binotel`."""
