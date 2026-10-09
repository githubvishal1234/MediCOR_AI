"""
Centralized error handling — Phase 8.

Every error path in the API (validation, expected HTTPExceptions,
database errors, and anything unexpected) is funneled through these
handlers so callers — the dashboard, the voice agent, and any future
API consumer — always get back the same JSON error shape:

    {"error": {"code": <str>, "message": <str>, "request_id": <str|None>}}

Nothing here changes status codes that routers already raise
deliberately (404, 400, 409, 401, 403 etc. via HTTPException) — it only
standardizes the response *body* shape and adds logging. Unexpected
exceptions (bugs, DB errors, etc.) are logged with a full stack trace
server-side and returned to the client as a generic 500 with no
internal detail leaked.
"""

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("errors")


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_body(code: str, message: str, request_id: str | None) -> dict:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body("http_error", str(exc.detail), _request_id(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        from fastapi.responses import JSONResponse

        # Pydantic's default error list can be verbose/nested; flatten to a
        # short, consistent message list that's still useful to a caller
        # without echoing internal type/schema names.
        problems = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            problems.append(f"{loc}: {err.get('msg')}" if loc else err.get("msg"))
        logger.info(f"req={_request_id(request)} validation error: {problems}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body("validation_error", "; ".join(problems) or "Invalid request", _request_id(request)),
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
        from fastapi.responses import JSONResponse

        logger.exception(f"req={_request_id(request)} database error on {request.method} {request.url.path}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                "database_error",
                "A database error occurred. Please try again.",
                _request_id(request),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        from fastapi.responses import JSONResponse

        logger.exception(f"req={_request_id(request)} unhandled error on {request.method} {request.url.path}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                "internal_error",
                "An unexpected error occurred. Please try again.",
                _request_id(request),
            ),
        )