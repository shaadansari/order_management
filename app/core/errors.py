"""The single error contract for the whole API.

WHY one error type: the design mandates that EVERY failure returns the same JSON shape
    {"error": <CODE>, "message": <human text>, "status": <http status>}
so clients handle errors predictably instead of parsing a dozen formats. Raise an
APIError (or a subclass) anywhere in services/routers and main.py converts it to JSON.
"""
from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, status_code: int, error: str, message: str):
        self.status_code = status_code
        self.error = error
        self.message = message
        super().__init__(message)


async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "message": exc.message, "status": exc.status_code},
    )


# --- Reusable subclasses so error codes/messages stay consistent across the codebase ---


class NotFoundError(APIError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(404, "NOT_FOUND", message)


class ForbiddenError(APIError):
    """403: we know who you are, but you're not allowed to do this."""

    def __init__(self, message: str = "You do not have permission to do this"):
        super().__init__(403, "FORBIDDEN", message)


class UnauthorizedError(APIError):
    """401: we don't know who you are (missing/invalid token)."""

    def __init__(self, message: str = "Authentication required"):
        super().__init__(401, "UNAUTHORIZED", message)
