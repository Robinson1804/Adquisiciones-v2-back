"""Unit tests for app.core.security — hash, verify, token lifecycle."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt

from app.config import settings
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def test_hash_roundtrip():
    plain = "MyS3cur3P@ss!"
    hashed = hash_password(plain)
    assert hashed.startswith("$2b$"), "Expected bcrypt hash"
    assert verify_password(plain, hashed) is True


def test_tampered_hash():
    plain = "MyS3cur3P@ss!"
    hashed = hash_password(plain)
    # Tamper a character in the salt region (indices 7..28), NOT the last char.
    # bcrypt's FINAL base64 char carries padding bits that bcrypt ignores, so
    # flipping it can still verify True (made this test flaky). Salt bytes are
    # always significant: changing the salt reliably breaks verification.
    idx = 15
    repl = "X" if hashed[idx] != "X" else "Y"
    tampered = hashed[:idx] + repl + hashed[idx + 1:]
    assert verify_password(plain, tampered) is False


def test_different_password_does_not_verify():
    hashed = hash_password("correct_password")
    assert verify_password("wrong_password", hashed) is False


# ---------------------------------------------------------------------------
# JWT create / decode
# ---------------------------------------------------------------------------
def test_create_and_decode_token():
    token = create_access_token(sub="42", username="alice", rol="EDITOR")
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["username"] == "alice"
    assert payload["rol"] == "EDITOR"
    assert "exp" in payload


def test_decode_expired_token():
    expired_payload = {
        "sub": "1",
        "username": "admin",
        "rol": "ADMIN",
        "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    expired_token = jwt.encode(
        expired_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    with pytest.raises(HTTPException) as exc_info:
        decode_token(expired_token)
    assert exc_info.value.status_code == 401


def test_decode_bad_signature():
    token = create_access_token(sub="1", username="admin", rol="ADMIN")
    # Tamper the FIRST char of the signature segment, NOT the last char of the
    # token. The last base64url char of a JWT signature carries padding bits
    # that don't affect the decoded bytes, so flipping it can still verify
    # (made this test flaky). The first signature char is always significant.
    parts = token.split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    tampered = ".".join(parts)
    with pytest.raises(HTTPException) as exc_info:
        decode_token(tampered)
    assert exc_info.value.status_code == 401


def test_decode_garbage_token():
    with pytest.raises(HTTPException) as exc_info:
        decode_token("not.a.token")
    assert exc_info.value.status_code == 401
