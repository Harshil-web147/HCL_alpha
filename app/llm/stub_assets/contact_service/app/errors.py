"""Uniform API error envelope and exception handlers.

Implements: FR-05, AC-06
Every error response has the shape::

    {"error": {"code": "<MACHINE_CODE>", "message": "<human text>", "details": [...]}}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Domain error that maps 1:1 to an HTTP status and machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str, details: list[Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


def error_body(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or []}}


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    details = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", []) if part != "body"]
        details.append({"field": ".".join(loc) or "body", "message": err.get("msg", "invalid value")})
    return details


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body("VALIDATION_ERROR", "Request validation failed", _validation_details(exc)),
        )
