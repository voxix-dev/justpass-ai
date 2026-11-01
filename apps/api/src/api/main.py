import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api import jobs

# 20,000 emoji written as \u escapes still fit.
MAX_BODY = 256 * 1024
MAX_ERRORS = 10

log = logging.getLogger("uvicorn.error")


class BodyLimit:
    """Refuse bodies over MAX_BODY with a 413, counting what arrives so chunked ones are covered."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > MAX_BODY:
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The request is too large")
            return message

        await self.app(scope, limited, send)


app = FastAPI(title="Homework API", version="0.0.0")
app.add_middleware(BodyLimit)
app.include_router(jobs.router)


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, error: RequestValidationError) -> Response:
    """Answer 422 without echoing the input, which can be huge or not encodable as JSON."""
    detail = [{k: v for k, v in e.items() if k != "input"} for e in error.errors()[:MAX_ERRORS]]
    body = json.dumps({"detail": detail}, default=str)
    return Response(body, status.HTTP_422_UNPROCESSABLE_CONTENT, media_type="application/json")


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
@app.exception_handler(PoolTimeoutError)
@app.exception_handler(OSError)
async def database_unavailable(_: Request, error: Exception) -> Response:
    """Answer 503 in the usual error shape when Postgres cannot be reached."""
    log.error("Database unavailable", exc_info=error)
    return JSONResponse(
        {"detail": "JustPass is having trouble. Try again in a moment."},
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@app.middleware("http")
async def api_prefix(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Serve requests under /api/ (Firebase Hosting forwards the prefix) from the root routes.

    The prefix goes into root_path rather than being cut from the path: routing ignores it,
    while generated URLs (slash redirects, the docs' schema URL) keep it.
    """
    prefix = request.scope.get("root_path", "") + "/api"
    if request.scope["path"].startswith(prefix + "/"):
        request.scope["root_path"] = prefix
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    """Whether the process is up. /ready also checks the database."""
    return {"status": "ok"}


@app.get("/ready", responses=jobs.UNAVAILABLE)
async def ready(session: jobs.Session) -> dict[str, str]:
    """Whether the API can reach Postgres."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
