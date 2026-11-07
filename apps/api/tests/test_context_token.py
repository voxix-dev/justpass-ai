import base64
import hashlib
import hmac
import json
import uuid
from datetime import timedelta

import pytest

from api.context_token import InvalidToken, mint_token, verify_token

JOB = uuid.uuid4()
LEASE = uuid.uuid4()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@pytest.fixture(autouse=True)
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_TOKEN_SECRET", "s" * 32)


def test_a_token_names_its_job_user_and_round():
    claims = verify_token(mint_token(JOB, "alice", 2, LEASE))
    assert (claims.job_id, claims.user_id, claims.round, claims.lease) == (JOB, "alice", 2, LEASE)


def test_a_token_has_no_padding():
    assert "=" not in mint_token(JOB, "alice", 1, LEASE)


def test_an_edited_token_is_rejected():
    payload, signature = mint_token(JOB, "alice", 1, LEASE).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["user_id"] = "bob"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    with pytest.raises(InvalidToken):
        verify_token(f"{forged}.{signature}")


@pytest.mark.parametrize("token", ["", "dev:alice", "abc.def", "a!b.c", "é.é", "."])
def test_garbage_is_rejected(token: str):
    with pytest.raises(InvalidToken):
        verify_token(token)


def test_a_signed_payload_with_other_claims_is_rejected():
    payload = b'{"job_id": 1}'
    signature = hmac.digest(b"s" * 32, payload, hashlib.sha256)
    token = f"{_b64(payload)}.{_b64(signature)}"
    with pytest.raises(InvalidToken):
        verify_token(token)


def test_an_expired_token_is_rejected():
    with pytest.raises(InvalidToken):
        verify_token(mint_token(JOB, "alice", 1, LEASE, ttl=timedelta(seconds=-1)))


def test_a_token_from_another_secret_is_rejected(monkeypatch: pytest.MonkeyPatch):
    token = mint_token(JOB, "alice", 1, LEASE)
    monkeypatch.setenv("CONTEXT_TOKEN_SECRET", "t" * 32)
    with pytest.raises(InvalidToken):
        verify_token(token)


@pytest.mark.parametrize("value", [None, "", "short", "s" * 31])
def test_a_missing_or_short_secret_is_invalid(monkeypatch: pytest.MonkeyPatch, value: str | None):
    token = mint_token(JOB, "alice", 1, LEASE)
    if value is None:
        monkeypatch.delenv("CONTEXT_TOKEN_SECRET")
    else:
        monkeypatch.setenv("CONTEXT_TOKEN_SECRET", value)
    with pytest.raises(InvalidToken, match="CONTEXT_TOKEN_SECRET"):
        verify_token(token)
    with pytest.raises(InvalidToken, match="CONTEXT_TOKEN_SECRET"):
        mint_token(JOB, "alice", 1, LEASE)
