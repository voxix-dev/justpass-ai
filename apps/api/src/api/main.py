from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

app = FastAPI(title="Homework API", version="0.0.0")


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
    return {"status": "ok"}
