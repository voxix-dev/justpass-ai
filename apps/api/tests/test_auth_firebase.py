"""Tokens are signed with a local key; the key set is injected, so nothing touches the network."""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from api.auth import firebase
from api.auth.firebase import AuthUser, FirebaseVerifier, InvalidIdTokenError

PROJECT = "demo-justpass"
KID = "key-1"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch) -> FirebaseVerifier:
    jwk = RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True) | {"kid": KID, "alg": "RS256"}
    client = jwt.PyJWKClient("https://keys.invalid/jwks")
    monkeypatch.setattr(client, "fetch_data", lambda: {"keys": [jwk]})
    return FirebaseVerifier(PROJECT, client)


def make_token(
    key: Any = KEY,
    kid: str | None = KID,
    alg: str = "RS256",
    provider: str = "google.com",
    **claims: Any,
) -> str:
    now = int(time.time())
    payload = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": "uid-123",
        "iat": now - 60,
        "auth_time": now - 60,
        "exp": now + 3600,
        "email": "ana@example.com",
        "email_verified": True,
        "firebase": {"sign_in_provider": provider},
    } | claims
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid} if kid else None)


def test_google_user(verifier: FirebaseVerifier):
    assert verifier.verify(make_token()) == AuthUser(
        uid="uid-123",
        is_anonymous=False,
        email="ana@example.com",
        email_verified=True,
        provider="google.com",
    )


def test_anonymous_user(verifier: FirebaseVerifier):
    user = verifier.verify(make_token(provider="anonymous", email=None))
    assert user.is_anonymous
    assert user.email is None


def test_email_link_user(verifier: FirebaseVerifier):
    user = verifier.verify(make_token(provider="password", email_verified=False))
    assert not user.is_anonymous
    assert user.email == "ana@example.com"
    assert not user.email_verified


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(make_token(exp=int(time.time()) - 60), id="expired"),
        pytest.param(make_token(aud="someone-else"), id="wrong audience"),
        pytest.param(
            make_token(iss="https://securetoken.google.com/someone-else"), id="wrong issuer"
        ),
        pytest.param(make_token(key=OTHER_KEY), id="bad signature"),
        pytest.param(make_token(kid="unknown"), id="unknown key"),
        pytest.param(make_token(kid=None), id="no key id"),
        pytest.param(make_token(aud=[PROJECT, "other"]), id="several audiences"),
        pytest.param(make_token(auth_time=None), id="no auth_time"),
        pytest.param(make_token(sub="u" * 129), id="uid too long"),
        pytest.param(make_token(key="s" * 32, alg="HS256"), id="HMAC instead of RSA"),
        pytest.param(make_token(key=None, alg="none"), id="unsigned"),
        pytest.param(make_token(iat=int(time.time()) + 600), id="issued in the future"),
        pytest.param(make_token(auth_time=int(time.time()) + 600), id="signed in in the future"),
        pytest.param(make_token(sub=""), id="empty uid"),
        pytest.param(make_token(firebase={}), id="no sign-in provider"),
        pytest.param("not-a-jwt", id="garbage"),
    ],
)
def test_rejects(verifier: FirebaseVerifier, token: str):
    with pytest.raises(InvalidIdTokenError):
        verifier.verify(token)


def test_emulator_accepts_unsigned_tokens_but_checks_claims():
    verifier = FirebaseVerifier(PROJECT, emulator=True)
    assert verifier.verify(make_token(key=None, alg="none")).uid == "uid-123"
    with pytest.raises(InvalidIdTokenError):
        verifier.verify(make_token(key=None, alg="none", aud="someone-else"))
    with pytest.raises(InvalidIdTokenError):
        verifier.verify(make_token(key=None, alg="none", exp=int(time.time()) - 60))


def test_emulator_refuses_real_projects():
    with pytest.raises(ValueError, match="demo-"):
        FirebaseVerifier("justpass-prod", emulator=True)


def test_key_endpoint_outage_is_not_a_bad_token(monkeypatch: pytest.MonkeyPatch):
    client = jwt.PyJWKClient("https://keys.invalid/jwks")

    def down() -> None:
        raise jwt.PyJWKClientConnectionError("down")

    monkeypatch.setattr(client, "fetch_data", down)
    with pytest.raises(jwt.PyJWKClientConnectionError):
        FirebaseVerifier(PROJECT, client).verify(make_token())


def test_verify_id_token_reads_env(monkeypatch: pytest.MonkeyPatch):
    firebase._verifier.cache_clear()
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")
    try:
        assert firebase.verify_id_token(make_token(key=None, alg="none")).uid == "uid-123"
    finally:
        firebase._verifier.cache_clear()


def test_requires_project_id():
    with pytest.raises(ValueError, match="FIREBASE_PROJECT_ID"):
        FirebaseVerifier("")
