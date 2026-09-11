set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# Show available project commands.
default:
    @just --list

# Install the locked project and development dependencies.
sync:
    uv sync --locked

# Refresh the dependency lockfile.
lock:
    uv lock

# Format code and apply safe lint fixes.
format:
    uv run ruff format .
    uv run ruff check --fix .

# Verify formatting without changing files.
format-check:
    uv run ruff format --check .

# Run Ruff lint checks.
lint:
    uv run ruff check .

# Run Pyright in the configured standard mode.
typecheck:
    uv run pyright

# Run the test suite, optionally forwarding arguments to pytest.
test *args:
    uv run pytest {{args}}

# Run tests with terminal coverage details.
coverage:
    uv run pytest --cov=binotel_api --cov-report=term-missing

# Run all non-mutating quality checks.
check: format-check lint typecheck test

# Run quality checks and build wheel/source distributions.
build: check
    uv build
