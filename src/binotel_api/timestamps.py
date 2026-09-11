"""Time types and conversion helpers for Binotel query parameters."""

from __future__ import annotations

from typing import TypeAlias

from whenever import Instant, OffsetDateTime, ZonedDateTime

from .exceptions import BinotelValidationError

ExactTime: TypeAlias = Instant | OffsetDateTime | ZonedDateTime
TimestampLike: TypeAlias = int | ExactTime


def _timestamp(value: TimestampLike, parameter: str) -> int:
    if isinstance(value, bool):
        raise BinotelValidationError(f"{parameter} must be an exact time or Unix timestamp")
    if isinstance(value, int):
        return value
    if isinstance(value, (Instant, OffsetDateTime, ZonedDateTime)):
        return value.timestamp()
    raise BinotelValidationError(f"{parameter} must be an exact time or Unix timestamp")


def _period(start_time: TimestampLike, stop_time: TimestampLike) -> dict[str, int]:
    start = _timestamp(start_time, "start_time")
    stop = _timestamp(stop_time, "stop_time")
    if stop < start:
        raise BinotelValidationError("stop_time must not be earlier than start_time")
    return {"startTime": start, "stopTime": stop}
