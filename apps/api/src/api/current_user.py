"""Who is calling: the `current_user` dependency every route uses.

Clients send `Authorization: Bearer <token>`. For now the only accepted token is the development
one, `dev:<name>`, and only while DEV_AUTH=1 (never set it in production). Its uid keeps the
`dev:` prefix, so it can never be a real Firebase user. Firebase ID token verification gets wired
in here later, so the routes and the API contract do not change.
"""

import os
import re
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

DEV_TOKEN = re.compile(r"dev:[A-Za-z0-9_-]{1,64}")

_bearer = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    uid: str
    is_anonymous: bool


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Return an anonymous dev user only while DEV_AUTH is exactly '1'.

    The token must be dev: followed by 1 to 64 ASCII letters, digits, underscores, or hyphens;
    the uid is the entire token. Raise HTTPException (401) with a Bearer challenge for missing
    or invalid credentials or when dev auth is disabled.
    """
    token = credentials.credentials if credentials else ""
    if os.environ.get("DEV_AUTH") == "1" and DEV_TOKEN.fullmatch(token):
        return CurrentUser(uid=token, is_anonymous=True)
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED, "missing or invalid token", {"WWW-Authenticate": "Bearer"}
    )
