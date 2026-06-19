"""Backend auth endpoint tests — maps to spec acceptance scenarios S-01..S-10."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends
from jose import jwt
from starlette.testclient import TestClient

from app.config import settings
from app.dependencies.auth import get_current_user, require_role
from app.main import app
from app.models.usuario import Usuario

# ---------------------------------------------------------------------------
# Test-only routes registered once at module level
# ---------------------------------------------------------------------------
@app.get("/test/protected", tags=["test"])
def _protected_route(user: Usuario = Depends(get_current_user)):
    return {"username": user.username}


@app.get("/test/admin-only", tags=["test"])
def _admin_only_route(user: Usuario = Depends(require_role("ADMIN"))):
    return {"username": user.username, "rol": user.rol}


@app.get("/test/editor-or-admin", tags=["test"])
def _editor_admin_route(user: Usuario = Depends(require_role("ADMIN", "EDITOR"))):
    return {"username": user.username}


# ---------------------------------------------------------------------------
# S-01: Successful login
# ---------------------------------------------------------------------------
def test_login_ok(client, admin_usuario):
    from tests.conftest import ADMIN_PASSWORD

    resp = client.post(
        "/auth/login",
        json={"username": admin_usuario.username, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert "user" in body
    user = body["user"]
    assert user["rol"] == "ADMIN"
    assert user["username"] == admin_usuario.username
    # password_hash MUST NOT appear anywhere in the response body
    assert "password_hash" not in str(body)


# ---------------------------------------------------------------------------
# S-02: Wrong password
# ---------------------------------------------------------------------------
def test_login_bad_password(client, admin_usuario):
    resp = client.post(
        "/auth/login",
        json={"username": admin_usuario.username, "password": "wrong_password"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Credenciales inválidas"


# ---------------------------------------------------------------------------
# S-03: Unknown username (same message as bad password — no enumeration)
# ---------------------------------------------------------------------------
def test_login_unknown_user(client):
    resp = client.post(
        "/auth/login",
        json={"username": "ghost_user_does_not_exist", "password": "any"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Credenciales inválidas"


# ---------------------------------------------------------------------------
# S-04: Inactive user
# ---------------------------------------------------------------------------
def test_login_inactive_user(client, inactive_usuario):
    resp = client.post(
        "/auth/login",
        json={"username": inactive_usuario.username, "password": "SomePass1!"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Usuario inactivo"


# ---------------------------------------------------------------------------
# S-05: Logout is stateless
# ---------------------------------------------------------------------------
def test_logout_stateless_no_auth(client):
    resp = client.post("/auth/logout")
    assert resp.status_code == 200
    assert "cerrada" in resp.json().get("detail", "")


def test_logout_stateless_with_auth(client, admin_headers):
    resp = client.post("/auth/logout", headers=admin_headers)
    assert resp.status_code == 200
    assert "cerrada" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# S-06: Protected route — no token
# ---------------------------------------------------------------------------
def test_protected_no_token(client):
    resp = client.get("/test/protected")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# S-07: Protected route — expired token
# ---------------------------------------------------------------------------
def test_protected_expired_token(client):
    expired_payload = {
        "sub": "999",
        "username": "ghost",
        "rol": "ADMIN",
        "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    expired_token = jwt.encode(
        expired_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    resp = client.get(
        "/test/protected",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# S-08: Protected route — invalid signature
# ---------------------------------------------------------------------------
def test_protected_invalid_signature(client, admin_token):
    # Tamper the FIRST char of the signature segment (the last token char carries
    # base64url padding bits that don't change the decoded signature → flaky).
    parts = admin_token.split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    tampered = ".".join(parts)
    resp = client.get(
        "/test/protected",
        headers={"Authorization": f"Bearer {tampered}"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# S-09: Insufficient role → 403
# ---------------------------------------------------------------------------
def test_protected_wrong_role(client, viewer_usuario, viewer_headers):
    resp = client.get("/test/admin-only", headers=viewer_headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Permisos insuficientes"


# ---------------------------------------------------------------------------
# S-10: Sufficient role — passes through
# ---------------------------------------------------------------------------
def test_protected_sufficient_role(client, admin_headers):
    resp = client.get("/test/editor-or-admin", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == "testadmin"
