from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

app = FastAPI(title="Homework API", version="0.0.0")


@app.middleware("http")
async def strip_api_prefix(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Strip /api from paths starting with /api/ before forwarding to root routes.

    Firebase Hosting forwards the prefix. Update the request scope in place, leaving
    other paths (including bare /api) unchanged. Return the downstream response;
    errors from call_next propagate to the caller.
    """
    if request.scope["path"].startswith("/api/"):
        request.scope["path"] = request.scope["path"].removeprefix("/api")
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
