"""Agent tokens: each one lets one role, in one round of one job, call the Context API.

The dispatcher mints one token per role with mint_token() when it starts a round, and hands them
to the agent run. A token is `<payload>.<signature>` in base64url: the payload is AgentClaims as
JSON, signed with HMAC-SHA256 under CONTEXT_TOKEN_SECRET. Only the api has the secret, so an
agent can read its token but cannot change the job, role or expiry in it.
"""

import base64
import hashlib
import hmac
import os
import time
import uuid
from datetime import timedelta

from pydantic import BaseModel, ValidationError

from shared.models import Role

TOKEN_TTL = timedelta(hours=1)
MIN_SECRET_LENGTH = 32


class AgentClaims(BaseModel):
    job_id: uuid.UUID
    round: int
    role: Role
    expires_at: int  # Unix time, seconds


class InvalidToken(Exception):
    pass


def _sign(payload: bytes) -> bytes:
    secret = os.environ.get("CONTEXT_TOKEN_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError("Set CONTEXT_TOKEN_SECRET to a random string of 32+ characters.")
    return hmac.digest(secret.encode(), payload, hashlib.sha256)


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _decode(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)


def mint_token(job_id: uuid.UUID, round_number: int, role: Role, ttl: timedelta = TOKEN_TTL) -> str:
    claims = AgentClaims(
        job_id=job_id,
        round=round_number,
        role=role,
        expires_at=int(time.time() + ttl.total_seconds()),
    )
    payload = claims.model_dump_json().encode()
    return f"{_encode(payload)}.{_encode(_sign(payload))}"


def verify_token(token: str) -> AgentClaims:
    """The token's claims. Raises InvalidToken unless we signed it and it has not expired."""
    payload, _, signature = token.partition(".")
    try:
        payload_bytes, signature_bytes = _decode(payload), _decode(signature)
    except ValueError as e:
        raise InvalidToken from e
    if not hmac.compare_digest(signature_bytes, _sign(payload_bytes)):
        raise InvalidToken
    try:
        claims = AgentClaims.model_validate_json(payload_bytes)
    except ValidationError as e:  # signed by an older version with other claims
        raise InvalidToken from e
    if claims.expires_at <= time.time():
        raise InvalidToken
    return claims
