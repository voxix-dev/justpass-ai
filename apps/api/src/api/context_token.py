"""Context API tokens: each one lets the agent of one job round call the Context API.

The dispatcher mints a token with mint_token() when it claims a round and hands it to the agent in
the run. A token is `<payload>.<signature>` in base64url without padding: the payload is
ContextClaims as JSON, signed with HMAC-SHA256 under CONTEXT_TOKEN_SECRET. Only the api has the
secret, so an agent can read its token but cannot change the job, user or expiry in it.
"""

import base64
import hashlib
import hmac
import os
import time
import uuid
from datetime import timedelta

from pydantic import BaseModel, ValidationError

TOKEN_TTL = timedelta(hours=2)
MIN_SECRET_LENGTH = 32


class ContextClaims(BaseModel):
    job_id: uuid.UUID
    user_id: str
    round: int
    lease: uuid.UUID  # the run that holds the round: a run that lost it can no longer write
    expires_at: int  # Unix time, seconds


class InvalidToken(Exception):
    """The token is missing, malformed, forged or expired, or the server has no usable secret."""


def secret() -> bytes:
    """The signing key from CONTEXT_TOKEN_SECRET. Raise InvalidToken if it is missing or short."""
    value = os.environ.get("CONTEXT_TOKEN_SECRET", "")
    if len(value) < MIN_SECRET_LENGTH:
        raise InvalidToken(f"Set CONTEXT_TOKEN_SECRET to {MIN_SECRET_LENGTH}+ random characters.")
    return value.encode()


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _decode(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)


def mint_token(
    job_id: uuid.UUID,
    user_id: str,
    round_number: int,
    lease: uuid.UUID,
    ttl: timedelta = TOKEN_TTL,
) -> str:
    """Sign a token for one round of one job and the lease that runs it, valid for ttl."""
    claims = ContextClaims(
        job_id=job_id,
        user_id=user_id,
        round=round_number,
        lease=lease,
        expires_at=int(time.time() + ttl.total_seconds()),
    )
    payload = claims.model_dump_json().encode()
    return f"{_encode(payload)}.{_encode(hmac.digest(secret(), payload, hashlib.sha256))}"


def verify_token(token: str) -> ContextClaims:
    """The token's claims. Raise InvalidToken unless we signed it and it has not expired."""
    payload, _, signature = token.partition(".")
    try:
        payload_bytes, signature_bytes = _decode(payload), _decode(signature)
    except ValueError as error:
        raise InvalidToken from error
    if not hmac.compare_digest(
        signature_bytes, hmac.digest(secret(), payload_bytes, hashlib.sha256)
    ):
        raise InvalidToken
    try:
        claims = ContextClaims.model_validate_json(payload_bytes)
    except ValidationError as error:  # signed by an older version with other claims
        raise InvalidToken from error
    if claims.expires_at <= time.time():
        raise InvalidToken
    return claims
