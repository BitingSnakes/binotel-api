class BinotelError(Exception):
    """Base exception for this package."""


class BinotelValidationError(BinotelError, ValueError):
    """Raised before a request when endpoint arguments are invalid."""


class BinotelRequestError(BinotelError):
    """Raised when the API cannot be reached or returns an error response."""
