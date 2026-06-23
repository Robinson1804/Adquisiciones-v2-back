"""Shared test fixtures.

Suppresses the cosmetic passlib/bcrypt version warning emitted by
passlib 1.7.4 when used with bcrypt 4.x — the warning does not affect
hash/verify correctness.
"""
import warnings

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.config import settings
from app.core.security import hash_password
from app.database import Base, get_db
from app.main import app
from app.models.usuario import Usuario

# ---------------------------------------------------------------------------
# Clean-slate fixture — wipes business tables before every test so that
# rows committed by the router (db.commit() required for pg_advisory_xact_lock)
# do not bleed across tests.  usuarios is intentionally preserved.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_business_tables():
    """Wipe business tables before each test, in FK-safe order.

    historial_cambios does NOT cascade from procesos, so it must be deleted
    first; deleting procesos then cascades to etapas_registro and montos_proceso.
    usuarios is intentionally preserved.
    """
    conn = _test_engine.connect()
    with conn.begin():
        conn.execute(text("DELETE FROM historial_cambios"))
        # etapa_archivos cascades via etapas_registro → procesos, but delete
        # explicitly as a defensive guard in case FK cascade order varies.
        conn.execute(text("DELETE FROM etapa_archivos"))
        # ingesta_documentos cascades from ingesta_correos (ON DELETE CASCADE);
        # delete ingesta_correos first so FK to procesos (SET NULL) doesn't conflict.
        conn.execute(text("DELETE FROM ingesta_correos"))
        conn.execute(text("DELETE FROM procesos"))
    conn.close()
    yield

# Suppress passlib bcrypt version read warning (cosmetic only)
warnings.filterwarnings(
    "ignore",
    message=".*error reading bcrypt version.*",
    category=UserWarning,
)

# ---------------------------------------------------------------------------
# In-process test database (uses the same Postgres DB, isolated via rollback)
# ---------------------------------------------------------------------------
_test_engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)
TestingSessionLocal = sessionmaker(
    bind=_test_engine,
    autoflush=False,
    autocommit=False,
    future=True,
)


@pytest.fixture(scope="function")
def db_session():
    """Provide a transactional DB session that rolls back after each test."""
    connection = _test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="function")
def client(db_session):
    """TestClient wired to the transactional test session."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth-specific fixtures
# ---------------------------------------------------------------------------
ADMIN_PASSWORD = "TestAdminPass1!"
EDITOR_PASSWORD = "TestEditorPass1!"
VIEWER_PASSWORD = "TestViewerPass1!"


@pytest.fixture(scope="function")
def admin_usuario(db_session) -> Usuario:
    """Active ADMIN user with a known password."""
    u = Usuario(
        username="testadmin",
        nombre_completo="Test Admin",
        rol="ADMIN",
        activo=True,
        area=None,
        password_hash=hash_password(ADMIN_PASSWORD),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture(scope="function")
def editor_usuario(db_session) -> Usuario:
    """Active EDITOR user with a known password."""
    u = Usuario(
        username="testeditor",
        nombre_completo="Test Editor",
        rol="EDITOR",
        activo=True,
        area=None,
        password_hash=hash_password(EDITOR_PASSWORD),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture(scope="function")
def editor_token(client, editor_usuario) -> str:
    """JWT for the editor test user."""
    resp = client.post(
        "/auth/login",
        json={"username": editor_usuario.username, "password": EDITOR_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="function")
def editor_headers(editor_token) -> dict:
    """Authorization header dict for the editor test user."""
    return {"Authorization": f"Bearer {editor_token}"}


@pytest.fixture(scope="function")
def viewer_usuario(db_session) -> Usuario:
    """Active VIEWER user."""
    u = Usuario(
        username="testviewer",
        nombre_completo="Test Viewer",
        rol="VIEWER",
        activo=True,
        area=None,
        password_hash=hash_password(VIEWER_PASSWORD),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture(scope="function")
def inactive_usuario(db_session) -> Usuario:
    """Inactive user (activo=False)."""
    u = Usuario(
        username="testinactive",
        nombre_completo="Inactive User",
        rol="VIEWER",
        activo=False,
        area=None,
        password_hash=hash_password("SomePass1!"),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture(scope="function")
def admin_token(client, admin_usuario) -> str:
    """JWT for the admin test user."""
    resp = client.post(
        "/auth/login",
        json={"username": admin_usuario.username, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="function")
def admin_headers(admin_token) -> dict:
    """Authorization header dict for the admin test user."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="function")
def viewer_token(client, viewer_usuario) -> str:
    """JWT for the viewer test user."""
    resp = client.post(
        "/auth/login",
        json={"username": viewer_usuario.username, "password": VIEWER_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="function")
def viewer_headers(viewer_token) -> dict:
    """Authorization header dict for the viewer test user."""
    return {"Authorization": f"Bearer {viewer_token}"}
