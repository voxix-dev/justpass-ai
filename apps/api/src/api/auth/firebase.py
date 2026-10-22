"""Verify Firebase ID tokens with PyJWT against Google's public signing keys.

We check what Firebase's docs list for third-party JWT libraries
(https://firebase.google.com/docs/auth/admin/verify-id-tokens): RS256 signature by a current
Google key (looked up by `kid`), `exp` in the future, `iat` and `auth_time` in the past,
`aud` = project ID, `iss` = https://securetoken.google.com/<project ID>, non-empty `sub`.
We don't check whether the user was disabled or the token revoked; that needs an Admin API call.

Environment:
- FIREBASE_PROJECT_ID: required.
- FIREBASE_AUTH_EMULATOR_HOST: set it (e.g. `localhost:9099`) when the app uses the Firebase
  Auth Emulator. The emulator does not sign its tokens, so signatures are NOT checked while it
  is set (all claims still are), the same as firebase-admin does. To keep that out of
  production, emulator mode only starts with a `demo-*` FIREBASE_PROJECT_ID
  (`firebase emulators:start --project demo-justpass`).
"""

import logging
import os
import time
from functools import cache
from typing import Any

import jwt
from jwt.types import Options
from pydantic import BaseModel

log = logging.getLogger(__name__)

JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)
# Google says to cache the keys for the response's max-age (about 6 h); 1 h is safely shorter.
# Unknown key IDs force a refresh anyway, at most every 30 s.
KEYS_CACHE_SECONDS = 3600
KEYS_TIMEOUT_SECONDS = 5
CLOCK_SKEW_SECONDS = 10
REQUIRED_CLAIMS = ["exp", "iat", "auth_time", "aud", "iss", "sub"]


class AuthUser(BaseModel):
    uid: str
    is_anonymous: bool
    email: str | None
    # Email/password users can sign up with someone else's address; never trust an unverified one.
    email_verified: bool
    # The `firebase.sign_in_provider` claim: "anonymous", "google.com", or "password" for
    # email link and email/password users.
    provider: str


class InvalidIdTokenError(Exception):
    """The token is malformed, expired, badly signed or for another project. Answer 401."""


class FirebaseVerifier:
    def __init__(
        self, project_id: str, keys: jwt.PyJWKClient | None = None, *, emulator: bool = False
    ) -> None:
        if not project_id:
            raise ValueError("FIREBASE_PROJECT_ID is not set")
        if emulator and not project_id.startswith("demo-"):
            raise ValueError("the Auth Emulator needs a demo-* FIREBASE_PROJECT_ID")
        self.project_id = project_id
        self.keys = keys or jwt.PyJWKClient(
            JWKS_URL, lifespan=KEYS_CACHE_SECONDS, timeout=KEYS_TIMEOUT_SECONDS
        )
        self.emulator = emulator
        if emulator:
            log.warning("Firebase Auth Emulator mode: ID token signatures are NOT checked")

    def verify(self, token: str) -> AuthUser:
        try:
            claims = self._decode(token)
        except jwt.PyJWKClientConnectionError:
            raise  # Google's key endpoint is down: a server error, not a bad token.
        except jwt.PyJWTError as e:
            raise InvalidIdTokenError(str(e)) from e

        now = time.time()
        sub = claims["sub"]
        if not isinstance(sub, str) or not 0 < len(sub) <= 128:
            raise InvalidIdTokenError("sub must be a non-empty string of at most 128 characters")
        auth_time = claims["auth_time"]
        if not isinstance(auth_time, int | float) or auth_time > now + CLOCK_SKEW_SECONDS:
            raise InvalidIdTokenError("auth_time must be in the past")
        firebase = claims.get("firebase")
        provider = firebase.get("sign_in_provider") if isinstance(firebase, dict) else None
        if not isinstance(provider, str):
            raise InvalidIdTokenError("firebase.sign_in_provider claim is missing")
        email = claims.get("email")
        return AuthUser(
            uid=sub,
            is_anonymous=provider == "anonymous",
            email=email if isinstance(email, str) else None,
            email_verified=claims.get("email_verified") is True,
            provider=provider,
        )

    def _decode(self, token: str) -> dict[str, Any]:
        # PyJWT turns the other checks off when the signature isn't checked, so list them all.
        checks: Options = {
            "verify_signature": not self.emulator,
            "strict_aud": True,
            "require": REQUIRED_CLAIMS,
            "verify_exp": True,
            "verify_iat": True,
            "verify_aud": True,
            "verify_iss": True,
        }
        key = "" if self.emulator else self.keys.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=self.project_id,
            issuer=f"https://securetoken.google.com/{self.project_id}",
            leeway=CLOCK_SKEW_SECONDS,
            options=checks,
        )


@cache
def _verifier() -> FirebaseVerifier:
    return FirebaseVerifier(
        os.environ.get("FIREBASE_PROJECT_ID", ""),
        emulator=bool(os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")),
    )


def verify_id_token(token: str) -> AuthUser:
    """Check a Firebase ID token and return who it belongs to. Raises InvalidIdTokenError."""
    return _verifier().verify(token)
