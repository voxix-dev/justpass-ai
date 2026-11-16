"""The signed-in user of a request, from the Firebase ID token in the Authorization header."""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.auth.firebase import AuthUser, InvalidIdTokenError, verify_id_token

_bearer = HTTPBearer(
    scheme_name="FirebaseIdToken",
    description="A Firebase Auth ID token of the signed-in user (anonymous or not).",
    auto_error=False,
)


async def _current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthUser:
    """401 unless the bearer token is a valid Firebase ID token; 503 if Google's keys are down."""
    if credentials is None:
        raise _unauthorized("Missing bearer token")
    try:
        # Verification is sync and may fetch Google's keys over the network (cached for an hour).
        return await run_in_threadpool(verify_id_token, credentials.credentials)
    except InvalidIdTokenError:
        raise _unauthorized("Invalid or expired token") from None
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Cannot reach Google's signing keys"
        ) from None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, {"WWW-Authenticate": "Bearer"})


CurrentUser = Annotated[AuthUser, Depends(_current_user)]
