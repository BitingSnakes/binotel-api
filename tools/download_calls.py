#!/usr/bin/env python3
"""Download Binotel call metadata and available recordings for a date range.

Credentials can be passed with ``--api-key`` and ``--api-secret`` or read from
``BINOTEL_API_KEY`` and ``BINOTEL_API_SECRET``. Command-line values take
precedence. The Binotel history endpoint accepts at most one day per request,
so this script walks the requested range one calendar day at a time.

Example::

    uv run python tools/download_calls.py --start 2026-09-01 --output calls
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from binotel_api import AsyncBinotel, BinotelConfig

_RATE_LIMIT_CODE = 106
_LOGGER = logging.getLogger("binotel-downloader")
_CONTENT_TYPE_SUFFIXES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
}


class ApiResponseError(RuntimeError):
    """Represent an error reported inside a successful HTTP response."""

    def __init__(self, code: int | None, message: str) -> None:
        self.code = code
        super().__init__(f"Binotel API error {code}: {message}")


@dataclass(slots=True)
class Counters:
    """Track download progress for the final manifest."""

    calls_found: int = 0
    metadata_written: int = 0
    recordings_downloaded: int = 0
    recordings_existing: int = 0
    recordings_unavailable: int = 0
    recording_errors: int = 0


class ApiRequester:
    """Rate-limit calls and retry Binotel's API-level throttling response."""

    def __init__(self, client: AsyncBinotel, delay: float, retries: int = 5) -> None:
        self.client = client
        self.delay = delay
        self.retries = retries
        self._next_request_at = 0.0
        self._lock = asyncio.Lock()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a successful response or raise an explicit API error."""
        async with self._lock:
            for attempt in range(1, self.retries + 1):
                await self._wait()
                result = await self.client.request(method, params)
                if not isinstance(result, dict):
                    raise ApiResponseError(None, "response is not a JSON object")

                status = result.get("status")
                if status == "success":
                    return result

                code = _optional_int(result.get("code"))
                message = str(result.get("message", "unknown error"))
                if code == _RATE_LIMIT_CODE and attempt < self.retries:
                    retry_delay = max(5.2, self.delay)
                    _LOGGER.warning(
                        "API rate limit reached; retrying %s in %.1f seconds (%d/%d)",
                        method,
                        retry_delay,
                        attempt,
                        self.retries,
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                raise ApiResponseError(code, message)

        raise ApiResponseError(_RATE_LIMIT_CODE, "retry limit reached")

    async def _wait(self) -> None:
        loop = asyncio.get_running_loop()
        remaining = self._next_request_at - loop.time()
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._next_request_at = loop.time() + self.delay


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _date_range(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _day_timestamps(day: date, timezone: ZoneInfo) -> tuple[int, int]:
    start = datetime.combine(day, datetime_time.min, timezone)
    stop = datetime.combine(day + timedelta(days=1), datetime_time.min, timezone)
    return int(start.timestamp()), int(stop.timestamp()) - 1


def _call_details(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = response.get("callDetails")
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ApiResponseError(None, "callDetails has an unexpected format")
    return value


def _call_id(call: Mapping[str, Any]) -> int:
    value = call.get("generalCallID", call.get("general_call_id"))
    result = _optional_int(value)
    if result is None or result < 0:
        raise ApiResponseError(None, "call record has no valid generalCallID")
    return result


def _atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _recording_url(response: Mapping[str, Any]) -> str | None:
    value = response.get("url")
    if isinstance(value, dict):
        value = value.get("url")
    return value if isinstance(value, str) and value.startswith(("https://", "http://")) else None


def _existing_recording(directory: Path, call_id: int) -> Path | None:
    for candidate in directory.glob(f"{call_id}.*"):
        if candidate.is_file() and not candidate.name.endswith(".part"):
            return candidate
    return None


def _safe_suffix(url: str, content_type: str | None) -> str:
    normalized_type = (content_type or "").partition(";")[0].strip().lower()
    if normalized_type in _CONTENT_TYPE_SUFFIXES:
        return _CONTENT_TYPE_SUFFIXES[normalized_type]
    suffix = Path(urlparse(url).path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return suffix
    return ".audio"


def _download_recording(url: str, directory: Path, call_id: int, timeout: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "binotel-api-call-downloader/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            suffix = _safe_suffix(url, response.headers.get("Content-Type"))
            destination = directory / f"{call_id}{suffix}"
            temporary = destination.with_suffix(destination.suffix + ".part")
            with temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(destination)
            return destination
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"recording download failed: {exc}") from exc


def _append_error(path: Path, day: date, call_id: int, error: Exception) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "date": day.isoformat(),
        "generalCallID": call_id,
        "error": str(error),
        "loggedAt": datetime.now().astimezone().isoformat(),
    }
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def _download_call(
    requester: ApiRequester,
    day: date,
    call: dict[str, Any],
    output: Path,
    counters: Counters,
    semaphore: asyncio.Semaphore,
    error_lock: asyncio.Lock,
    *,
    metadata_only: bool,
    overwrite: bool,
    http_timeout: int,
) -> None:
    async with semaphore:
        call_id = _call_id(call)
        counters.calls_found += 1

        metadata_path = output / day.isoformat() / f"{call_id}.json"
        if overwrite or not metadata_path.exists():
            await asyncio.to_thread(_atomic_json_write, metadata_path, call)
            counters.metadata_written += 1
            _LOGGER.debug("Call %d: wrote metadata to %s", call_id, metadata_path)

        if metadata_only:
            return

        recording_directory = output / day.isoformat() / "recordings"
        if not overwrite and _existing_recording(recording_directory, call_id) is not None:
            counters.recordings_existing += 1
            _LOGGER.debug("Call %d: recording already exists", call_id)
            return

        try:
            record_response = await requester.request(
                "stats/call-record", {"generalCallID": call_id}
            )
            url = _recording_url(record_response)
            if url is None:
                counters.recordings_unavailable += 1
                _LOGGER.debug("Call %d: recording is unavailable", call_id)
                return
            destination = await asyncio.to_thread(
                _download_recording, url, recording_directory, call_id, http_timeout
            )
            counters.recordings_downloaded += 1
            _LOGGER.debug("Call %d: downloaded recording to %s", call_id, destination)
        except (ApiResponseError, RuntimeError) as exc:
            counters.recording_errors += 1
            _LOGGER.warning("Call %d: %s", call_id, exc)
            async with error_lock:
                await asyncio.to_thread(_append_error, output / "errors.jsonl", day, call_id, exc)


async def _download_day(
    requester: ApiRequester,
    day: date,
    timezone: ZoneInfo,
    output: Path,
    counters: Counters,
    semaphore: asyncio.Semaphore,
    error_lock: asyncio.Lock,
    *,
    metadata_only: bool,
    overwrite: bool,
    http_timeout: int,
    progress_every: int,
    seen: set[int],
) -> None:
    _LOGGER.info("Fetching calls for %s", day.isoformat())
    start_time, stop_time = _day_timestamps(day, timezone)
    response = await requester.request(
        "stats/list-of-calls-for-period",
        {"startTime": start_time, "stopTime": stop_time},
    )
    calls = _call_details(response)
    unique_calls: list[dict[str, Any]] = []
    for call in calls:
        call_id = _call_id(call)
        if call_id not in seen:
            seen.add(call_id)
            unique_calls.append(call)
    total = len(unique_calls)
    _LOGGER.info("%s: found %d new call records", day.isoformat(), total)

    tasks = [
        asyncio.create_task(
            _download_call(
                requester,
                day,
                call,
                output,
                counters,
                semaphore,
                error_lock,
                metadata_only=metadata_only,
                overwrite=overwrite,
                http_timeout=http_timeout,
            )
        )
        for call in unique_calls
    ]
    for completed, task in enumerate(asyncio.as_completed(tasks), start=1):
        await task
        if completed % progress_every == 0 or completed == total:
            _LOGGER.info(
                "%s: processed %d/%d calls (downloaded=%d, existing=%d, unavailable=%d, errors=%d)",
                day.isoformat(),
                completed,
                total,
                counters.recordings_downloaded,
                counters.recordings_existing,
                counters.recordings_unavailable,
                counters.recording_errors,
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-key",
        help="Binotel API key; defaults to BINOTEL_API_KEY",
    )
    parser.add_argument(
        "--api-secret",
        help="Binotel API secret; defaults to BINOTEL_API_SECRET",
    )
    parser.add_argument(
        "--start",
        required=True,
        type=_date_argument,
        help="first date to download, inclusive (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=_date_argument,
        help="last date to download, inclusive; defaults to today in --timezone",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("binotel-calls"),
        help="local destination directory (default: binotel-calls)",
    )
    parser.add_argument("--timezone", default="Europe/Kyiv", help="calendar timezone")
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="save call JSON without downloading audio recordings",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing metadata and recording files",
    )
    parser.add_argument(
        "--api-delay",
        type=float,
        default=1.1,
        help="minimum seconds between Binotel API requests (default: 1.1)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=15,
        help="maximum concurrent call downloads (default: 15)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10,
        help="log progress after this many calls (default: 10)",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="logging verbosity (default: INFO)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds (default: 30)",
    )
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    """Run the asynchronous downloader."""
    if args.api_delay < 0:
        raise SystemExit("--api-delay must not be negative")
    if args.concurrency <= 0:
        raise SystemExit("--concurrency must be positive")
    if args.progress_every <= 0:
        raise SystemExit("--progress-every must be positive")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")

    try:
        timezone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"unknown timezone: {args.timezone}") from exc

    end = args.end or datetime.now(timezone).date()
    if end < args.start:
        raise SystemExit("--end must not be earlier than --start")

    api_key = args.api_key or os.getenv("BINOTEL_API_KEY")
    api_secret = args.api_secret or os.getenv("BINOTEL_API_SECRET")
    if not api_key or not api_secret:
        raise SystemExit(
            "pass --api-key and --api-secret or set BINOTEL_API_KEY and BINOTEL_API_SECRET"
        )

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    counters = Counters()
    seen: set[int] = set()
    semaphore = asyncio.Semaphore(args.concurrency)
    error_lock = asyncio.Lock()
    config = BinotelConfig.from_env()
    config = BinotelConfig(
        url=config.url,
        version=config.version,
        response_format=config.response_format,
        key=api_key,
        secret=api_secret,
        timeout=args.timeout,
        connect_timeout=min(config.connect_timeout, args.timeout),
        retry_times=config.retry_times,
        retry_sleep_ms=config.retry_sleep_ms,
        retry_max_sleep_ms=config.retry_max_sleep_ms,
        throttle_ms=0,
    )

    started_at = datetime.now().astimezone()
    _LOGGER.info(
        "Starting download: %s through %s, output=%s, concurrency=%d, metadata_only=%s",
        args.start,
        end,
        output,
        args.concurrency,
        args.metadata_only,
    )
    async with AsyncBinotel(config) as client:
        requester = ApiRequester(client, args.api_delay)
        for day in _date_range(args.start, end):
            await _download_day(
                requester,
                day,
                timezone,
                output,
                counters,
                semaphore,
                error_lock,
                metadata_only=args.metadata_only,
                overwrite=args.overwrite,
                http_timeout=args.timeout,
                progress_every=args.progress_every,
                seen=seen,
            )

    manifest = {
        "startDate": args.start.isoformat(),
        "endDate": end.isoformat(),
        "timezone": str(timezone),
        "metadataOnly": args.metadata_only,
        "concurrency": args.concurrency,
        "startedAt": started_at.isoformat(),
        "finishedAt": datetime.now().astimezone().isoformat(),
        **{name: getattr(counters, name) for name in counters.__dataclass_fields__},
    }
    await asyncio.to_thread(_atomic_json_write, output / "manifest.json", manifest)
    _LOGGER.info(
        "Finished: calls=%d, metadata=%d, downloaded=%d, existing=%d, unavailable=%d, errors=%d",
        counters.calls_found,
        counters.metadata_written,
        counters.recordings_downloaded,
        counters.recordings_existing,
        counters.recordings_unavailable,
        counters.recording_errors,
    )
    _LOGGER.debug("Manifest:\n%s", json.dumps(manifest, indent=2))
    return 0


def main() -> int:
    """Parse arguments and run the asynchronous downloader."""
    args = _parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("Interrupted; existing downloads are safe to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
