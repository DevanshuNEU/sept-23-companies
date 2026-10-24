"""One error shape for every failure: `{code, message, checks}`.

A handler raises `ApiProblem`; framework errors (bad input, unknown path, wrong method) and unhandled exceptions are
turned into the same envelope, so the web app has one thing to parse. An unhandled exception is logged with its
traceback and never with the request body, and the client is told that nothing was saved.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from outreach.api.models.base import Wire
from outreach.api.models.checks import Check

log = logging.getLogger("outreach.api")

STATUS: dict[str, int] = {
    "blocked": 422,
    "conflict": 409,
    "capacity": 409,
    "not_allowed": 409,
    "not_found": 404,
    "forbidden": 403,
    "bad_request": 400,
    "idempotency_mismatch": 422,
    "busy": 503,
}
SERVER_ERROR_MESSAGE = "The server had a problem. Nothing was saved."


class ErrorEnvelope(Wire):
    code: str
    message: str
    checks: list[Check]


class ApiProblem(Exception):
    """A refusal the client should see: a code from `STATUS`, a plain message, and checks when a block has them."""

    def __init__(self, code: str, message: str, checks: list | None = None, headers: dict[str, str] | None = None):
        if code not in STATUS:
            raise ValueError(f"unknown error code {code!r}; use one of {sorted(STATUS)}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.checks = list(checks or [])
        self.headers = headers or {}

    @property
    def status(self) -> int:
        return STATUS[self.code]


def envelope(code: str, message: str, status: int, checks: list | None = None, headers: dict | None = None):
    body = ErrorEnvelope(code=code, message=message, checks=checks or []).model_dump(by_alias=True, mode="json")
    return JSONResponse(body, status_code=status, headers=headers)


def _field_message(error: dict) -> str:
    loc = list(error.get("loc", ()))
    source = loc[0] if loc and loc[0] in ("body", "query", "path", "header", "cookie") else None
    rest = loc[1:] if source else loc
    name = ""
    for part in rest:
        if isinstance(part, int):
            if name:  # a leading number is a character position in bad JSON, not a field
                name += f"[{part}]"
        else:
            name += f".{part}" if name else str(part)
    return f"{name or source or 'request'}: {error.get('msg', 'Invalid value')}"


async def _problem(_: Request, exc: ApiProblem):
    return envelope(exc.code, exc.message, exc.status, exc.checks, exc.headers)


async def _validation(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    message = _field_message(errors[0]) if errors else "The request was not valid."
    return envelope("bad_request", message, 400)  # never echo the input: it may be a draft


async def _http(_: Request, exc: StarletteHTTPException):
    if exc.status_code >= 500:
        return envelope("server_error", SERVER_ERROR_MESSAGE, exc.status_code, headers=exc.headers)
    code = {404: "not_found", 403: "forbidden"}.get(exc.status_code, "bad_request")
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else "The request was not allowed."
    return envelope(code, message, exc.status_code, headers=exc.headers)


async def _unhandled(request: Request, exc: Exception):
    log.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return envelope("server_error", SERVER_ERROR_MESSAGE, 500)


def install(app: FastAPI) -> None:
    app.add_exception_handler(ApiProblem, _problem)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(Exception, _unhandled)
