"""Configuration loaded explicitly or from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


@dataclass(frozen=True, slots=True)
class BinotelConfig:
    url: str = "https://api.binotel.com/api/"
    version: str = "4.0"
    response_format: str = "json"
    key: str | None = None
    secret: str | None = None
    timeout: int = 15
    connect_timeout: int = 10
    retry_times: int = 5
    retry_sleep_ms: int = 1_000
    retry_max_sleep_ms: int = 15_000
    throttle_ms: int = 200

    @classmethod
    def from_env(cls) -> BinotelConfig:
        defaults = cls()
        return cls(
            url=os.getenv("BINOTEL_API_URL", defaults.url),
            version=os.getenv("BINOTEL_API_VERSION", defaults.version),
            response_format=os.getenv("BINOTEL_API_FORMAT", defaults.response_format),
            key=os.getenv("BINOTEL_API_KEY"),
            secret=os.getenv("BINOTEL_API_SECRET"),
            timeout=_env_int("BINOTEL_API_TIMEOUT", defaults.timeout),
            connect_timeout=_env_int("BINOTEL_API_CONNECT_TIMEOUT", defaults.connect_timeout),
            retry_times=_env_int("BINOTEL_API_RETRY_TIMES", defaults.retry_times),
            retry_sleep_ms=_env_int("BINOTEL_API_RETRY_SLEEP", defaults.retry_sleep_ms),
            retry_max_sleep_ms=_env_int("BINOTEL_API_RETRY_MAX_SLEEP", defaults.retry_max_sleep_ms),
            throttle_ms=_env_int("BINOTEL_API_THROTTLE_MS", defaults.throttle_ms),
        )
