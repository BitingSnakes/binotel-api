# Binotel API for Python

A typed synchronous and asynchronous Python client for Binotel API 4.0, with
optional FastAPI webhook routing.

## Installation

```bash
pip install .
```

Install the optional FastAPI webhook integration with:

```bash
pip install ".[webhooks]"
```

For development:

```bash
uv sync
just check
```

Run `just` to list all project commands. Common recipes include `just format`,
`just test`, `just typecheck`, `just coverage`, and `just build`.

Python 3.11 or newer is required.

## Configuration

The client reads these environment variables:

```dotenv
BINOTEL_API_KEY=your-key
BINOTEL_API_SECRET=your-secret
BINOTEL_API_URL=https://api.binotel.com/api/
BINOTEL_API_VERSION=4.0
BINOTEL_API_FORMAT=json
BINOTEL_API_TIMEOUT=15
BINOTEL_API_CONNECT_TIMEOUT=10
BINOTEL_API_RETRY_TIMES=5
BINOTEL_API_RETRY_SLEEP=1000
BINOTEL_API_RETRY_MAX_SLEEP=15000
BINOTEL_API_THROTTLE_MS=200
```

Configuration can also be supplied directly with `BinotelConfig`.

## API usage

```python
from binotel_api import Binotel

with Binotel() as binotel:
    customers = binotel.customers.list()
    calls = binotel.stats.incoming_calls_for_period(1_725_840_000, 1_725_926_400)

for customer in customers:
    print(customer.id, customer.name)
```

For asynchronous applications, use `AsyncBinotel` with native wreq async I/O:

```python
import asyncio

from binotel_api import AsyncBinotel


async def main() -> None:
    async with AsyncBinotel() as binotel:
        customers = await binotel.customers.list()
        calls = await binotel.stats.online_calls()

    print(len(customers), len(calls))


asyncio.run(main())
```

The client provides four resource groups:

- `binotel.customers`
- `binotel.stats`
- `binotel.settings`
- `binotel.calls`

Method names are Pythonic `snake_case`; outbound Binotel parameters remain in
their documented `camelCase` format. Responses are Pydantic models from
`binotel_api.schemas`.

## Original source

This Python implementation was developed from
[sashalenz/binotel-api](https://github.com/sashalenz/binotel-api).

## FastAPI webhooks

This section requires the `webhooks` installation extra shown above.

```python
from fastapi import FastAPI
from binotel_api.webhooks import ReceivedTheCall, create_webhook_router


class SaveReceivedCall(ReceivedTheCall):
    async def handle(self, data):
        # Persist or enqueue data here.
        return {"accepted": True}


app = FastAPI()
app.include_router(
    create_webhook_router(
        actions={"receivedTheCall": SaveReceivedCall},
    )
)
```

By default, webhook requests are accepted only from Binotel's IP allowlist. If
the application is behind a trusted reverse proxy, enable
`trust_forwarded_for=True` only when that proxy replaces the incoming
`X-Forwarded-For` header.

The webhook endpoint is `POST /binotel-api/webhook`.

## License

MIT. See [LICENSE.md](LICENSE.md).

## Authors

- Oleksandr Petrovskyi (`sashalenz@gmail.com`)
- Yehor Smoliakov (`egorsmkv@gmail.com`)
