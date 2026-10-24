import base64
import json
import uuid
from datetime import timedelta

import pytest

from api.agent_token import InvalidToken, mint_token, verify_token
from shared.models import Role

JOB = uuid.uuid4()


@pytest.fixture(autouse=True)
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_TOKEN_SECRET", "s" * 32)


def test_a_token_names_its_job_round_and_role():
    claims = verify_token(mint_token(JOB, 2, Role.WRITER))
    assert (claims.job_id, claims.round, claims.role) == (JOB, 2, Role.WRITER)


def test_an_edited_token_is_rejected():
    payload, signature = mint_token(JOB, 1, Role.REVIEW).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["role"] = "orchestrator"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    with pytest.raises(InvalidToken):
        verify_token(f"{forged}.{signature}")


@pytest.mark.parametrize("token", ["", "dev:alice", "abc.def", "a!b.c", "é.é"])
def test_garbage_is_rejected(token: str):
    with pytest.raises(InvalidToken):
        verify_token(token)


def test_expired_and_foreign_tokens_are_rejected(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(InvalidToken):
        verify_token(mint_token(JOB, 1, Role.WRITER, ttl=timedelta(seconds=-1)))
    token = mint_token(JOB, 1, Role.WRITER)
    monkeypatch.setenv("CONTEXT_TOKEN_SECRET", "t" * 32)
    with pytest.raises(InvalidToken):
        verify_token(token)


def test_tokens_need_a_long_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONTEXT_TOKEN_SECRET", "short")
    with pytest.raises(RuntimeError, match="CONTEXT_TOKEN_SECRET"):
        mint_token(JOB, 1, Role.WRITER)
