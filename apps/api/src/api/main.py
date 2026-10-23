from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

app = FastAPI(title="Homework API", version="0.0.0")


@app.middleware("http")
async def strip_api_prefix(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Firebase Hosting forwards /api/** with the prefix; routes are defined at the root."""
    if request.scope["path"].startswith("/api/"):
        request.scope["path"] = request.scope["path"].removeprefix("/api")
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
