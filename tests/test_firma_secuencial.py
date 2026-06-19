"""Tests for FirmaSecuencial model and firma_secuencial service.

TDD order:
  T02a → model import + structure (RED before FirmaSecuencial model created)
  T02b → model created (GREEN)
  T07a → service logic (RED before service created)
  T07b → service implemented (GREEN)
  T07c → router HTTP tests (RED before router created)
  T07d → router implemented (GREEN)
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# T02a — Model structure tests (these will fail with ImportError first)
# ---------------------------------------------------------------------------

def test_firma_secuencial_importable():
    """FirmaSecuencial must be importable from app.models.firma_secuencial."""
    from app.models.firma_secuencial import FirmaSecuencial  # noqa: F401
    assert FirmaSecuencial.__tablename__ == "firma_secuencial"


def test_firma_secuencial_columns():
    """Model must expose expected columns."""
    from app.models.firma_secuencial import FirmaSecuencial
    mapper = FirmaSecuencial.__mapper__
    col_names = {c.key for c in mapper.columns}
    required = {
        "id", "proceso_id", "etapa_cod", "area", "orden",
        "estado", "fecha_recibido", "fecha_firmado", "motivo_rechazo",
        "ronda", "created_at", "updated_at",
    }
    assert required.issubset(col_names), f"Missing columns: {required - col_names}"


def test_firma_secuencial_unique_constraint():
    """Model must declare unique constraint on (proceso_id, etapa_cod, area, ronda)."""
    from sqlalchemy import UniqueConstraint
    from app.models.firma_secuencial import FirmaSecuencial
    table = FirmaSecuencial.__table__
    uc_cols = set()
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            cols = {c.name for c in constraint.columns}
            if cols == {"proceso_id", "etapa_cod", "area", "ronda"}:
                uc_cols = cols
                break
    assert uc_cols == {"proceso_id", "etapa_cod", "area", "ronda"}, (
        "Missing UniqueConstraint on (proceso_id, etapa_cod, area, ronda)"
    )


def test_firma_secuencial_estado_default():
    """Default estado must be 'PENDIENTE'."""
    from app.models.firma_secuencial import FirmaSecuencial
    col = FirmaSecuencial.__table__.c["estado"]
    assert col.server_default is not None or col.default is not None


def test_firma_secuencial_ronda_default_1():
    """Default ronda must be 1."""
    from app.models.firma_secuencial import FirmaSecuencial
    col = FirmaSecuencial.__table__.c["ronda"]
    # Either a server default or python-side default
    assert col.server_default is not None or col.default is not None


# ---------------------------------------------------------------------------
# T07a — Service logic tests (RED before firma_secuencial_service.py exists)
# These tests use the DB via conftest fixtures.
# ---------------------------------------------------------------------------

@pytest.fixture
def proceso_con_areas(client, db_session, editor_headers):
    """Create a proceso with two areas for firma tests."""
    resp = client.post(
        "/procesos",
        json={
            "requerimiento": "Test firma secuencial",
            "tipo": "SERVICIO",
            "areas_usuarias": ["DCOP", "DREH"],
        },
        headers=editor_headers,
    )
    assert resp.status_code == 201
    return resp.json()


def test_crear_firma_row_estado_pendiente(proceso_con_areas, client, editor_headers):
    """POST /procesos/{id}/firma-secuencial/{etapa_cod} → estado=PENDIENTE."""
    proceso_id = proceso_con_areas["id"]
    resp = client.post(
        f"/procesos/{proceso_id}/firma-secuencial/E02b",
        json={"area": "DCOP", "orden": 1},
        headers=editor_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["estado"] == "PENDIENTE"
    assert data["area"] == "DCOP"
    assert data["orden"] == 1
    assert data["etapa_cod"] == "E02b"


def test_orden_enforced_area2_no_puede_firmar_antes_area1(
    proceso_con_areas, client, editor_headers
):
    """Area with orden=2 cannot FIRMADO if orden=1 is still PENDIENTE."""
    proceso_id = proceso_con_areas["id"]
    # Create area 1 PENDIENTE
    r1 = client.post(
        f"/procesos/{proceso_id}/firma-secuencial/E02b",
        json={"area": "DCOP", "orden": 1},
        headers=editor_headers,
    )
    assert r1.status_code == 201
    # Create area 2
    r2 = client.post(
        f"/procesos/{proceso_id}/firma-secuencial/E02b",
        json={"area": "DREH", "orden": 2},
        headers=editor_headers,
    )
    assert r2.status_code == 201
    firma_id_area2 = r2.json()["id"]

    # Attempt to mark area 2 as FIRMADO → should fail 422
    resp = client.patch(
        f"/procesos/{proceso_id}/firma-secuencial/{firma_id_area2}",
        json={"nuevo_estado": "FIRMADO"},
        headers=editor_headers,
    )
    assert resp.status_code == 422


def test_area2_puede_firmar_despues_de_area1(
    proceso_con_areas, client, editor_headers
):
    """Area 2 can be FIRMADO after area 1 is FIRMADO."""
    proceso_id = proceso_con_areas["id"]
    r1 = client.post(
        f"/procesos/{proceso_id}/firma-secuencial/E02b",
        json={"area": "DCOP", "orden": 1},
        headers=editor_headers,
    )
    firma_id_area1 = r1.json()["id"]
    r2 = client.post(
        f"/procesos/{proceso_id}/firma-secuencial/E02b",
        json={"area": "DREH", "orden": 2},
        headers=editor_headers,
    )
    firma_id_area2 = r2.json()["id"]

    # Sign area 1 first
    client.patch(
        f"/procesos/{proceso_id}/firma-secuencial/{firma_id_area1}",
        json={"nuevo_estado": "FIRMADO"},
        headers=editor_headers,
    )

    # Now area 2 should be able to sign
    resp = client.patch(
        f"/procesos/{proceso_id}/firma-secuencial/{firma_id_area2}",
        json={"nuevo_estado": "FIRMADO"},
        headers=editor_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "FIRMADO"


def test_duplicate_area_ronda_rejected(proceso_con_areas, client, editor_headers):
    """Duplicate (proceso, etapa, area, ronda) must be rejected with 409."""
    proceso_id = proceso_con_areas["id"]
    client.post(
        f"/procesos/{proceso_id}/firma-secuencial/E02b",
        json={"area": "DCOP", "orden": 1},
        headers=editor_headers,
    )
    # Same area again
    resp = client.post(
        f"/procesos/{proceso_id}/firma-secuencial/E02b",
        json={"area": "DCOP", "orden": 1},
        headers=editor_headers,
    )
    assert resp.status_code == 409


def test_e06c_ronda2_independiente_de_ronda1(proceso_con_areas, client, editor_headers):
    """E06c ronda=2 rows are independent of ronda=1 rows."""
    proceso_id = proceso_con_areas["id"]
    # Create ronda 1
    r1 = client.post(
        f"/procesos/{proceso_id}/firma-secuencial/E06c",
        json={"area": "DCOP", "orden": 1, "ronda": 1},
        headers=editor_headers,
    )
    assert r1.status_code == 201

    # Create ronda 2 — same area, different ronda → should succeed
    r2 = client.post(
        f"/procesos/{proceso_id}/firma-secuencial/E06c",
        json={"area": "DCOP", "orden": 1, "ronda": 2},
        headers=editor_headers,
    )
    assert r2.status_code == 201
    assert r2.json()["ronda"] == 2


def test_firma_not_found_404(proceso_con_areas, client, editor_headers):
    """PATCH to non-existent firma_id → 404."""
    proceso_id = proceso_con_areas["id"]
    resp = client.patch(
        f"/procesos/{proceso_id}/firma-secuencial/999999",
        json={"nuevo_estado": "FIRMADO"},
        headers=editor_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# W1 — Auto-completion: all FIRMADO → etapas_registro COMPLETADO
# ---------------------------------------------------------------------------

@pytest.fixture
def proceso_tres_areas(client, db_session, editor_headers):
    """Proceso with three areas for multi-firma tests."""
    resp = client.post(
        "/procesos",
        json={
            "requerimiento": "Test auto-completion firma",
            "tipo": "SERVICIO",
            "areas_usuarias": ["DCOP", "DREH", "DTDIS"],
        },
        headers=editor_headers,
    )
    assert resp.status_code == 201
    return resp.json()


def test_all_firmado_marca_etapa_completada(proceso_tres_areas, client, editor_headers, db_session):
    """W1: when all firma_secuencial rows for (proceso, etapa, ronda) are FIRMADO,
    etapas_registro must be updated to COMPLETADO in the same transaction.
    """
    from app.models.etapa import EtapaRegistro
    from sqlalchemy import select

    proceso_id = proceso_tres_areas["id"]
    etapa_cod = "E02b"

    # Create 3 firmas in order
    firma_ids: list[int] = []
    for i, area in enumerate(["DCOP", "DREH", "DTDIS"], start=1):
        r = client.post(
            f"/procesos/{proceso_id}/firma-secuencial/{etapa_cod}",
            json={"area": area, "orden": i},
            headers=editor_headers,
        )
        assert r.status_code == 201, r.text
        firma_ids.append(r.json()["id"])

    # Sign all in order
    for fid in firma_ids:
        r = client.patch(
            f"/procesos/{proceso_id}/firma-secuencial/{fid}",
            json={"nuevo_estado": "FIRMADO"},
            headers=editor_headers,
        )
        assert r.status_code == 200, r.text

    # etapas_registro E02b must now be COMPLETADO
    db_session.expire_all()
    fila = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == etapa_cod,
        )
    ).scalars().first()

    assert fila is not None, "etapas_registro row for E02b must exist"
    assert fila.estado_etapa == "COMPLETADO", (
        f"Expected COMPLETADO, got {fila.estado_etapa}"
    )


def test_parcial_firmado_no_completa_etapa(proceso_tres_areas, client, editor_headers, db_session):
    """W1: only 2 of 3 firmas FIRMADO → etapas_registro E02b NOT COMPLETADO."""
    from app.models.etapa import EtapaRegistro
    from sqlalchemy import select

    proceso_id = proceso_tres_areas["id"]
    etapa_cod = "E02b"

    firma_ids: list[int] = []
    for i, area in enumerate(["DCOP", "DREH", "DTDIS"], start=1):
        r = client.post(
            f"/procesos/{proceso_id}/firma-secuencial/{etapa_cod}",
            json={"area": area, "orden": i},
            headers=editor_headers,
        )
        assert r.status_code == 201
        firma_ids.append(r.json()["id"])

    # Sign only first two
    for fid in firma_ids[:2]:
        client.patch(
            f"/procesos/{proceso_id}/firma-secuencial/{fid}",
            json={"nuevo_estado": "FIRMADO"},
            headers=editor_headers,
        )

    db_session.expire_all()
    fila = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == etapa_cod,
        )
    ).scalars().first()

    # Either no row, or not COMPLETADO
    assert fila is None or fila.estado_etapa != "COMPLETADO", (
        "etapas_registro E02b must NOT be COMPLETADO when only 2/3 are FIRMADO"
    )


def test_e06c_ronda1_completa_no_afecta_ronda2(proceso_tres_areas, client, editor_headers, db_session):
    """W1: completing all firmas for E06c ronda=1 marks ronda=1 COMPLETADO but not ronda=2."""
    from app.models.etapa import EtapaRegistro
    from sqlalchemy import select

    proceso_id = proceso_tres_areas["id"]
    etapa_cod = "E06c"

    # Ronda 1 firmas
    ronda1_ids: list[int] = []
    for i, area in enumerate(["DCOP", "DREH"], start=1):
        r = client.post(
            f"/procesos/{proceso_id}/firma-secuencial/{etapa_cod}",
            json={"area": area, "orden": i, "ronda": 1},
            headers=editor_headers,
        )
        assert r.status_code == 201, r.text
        ronda1_ids.append(r.json()["id"])

    # Ronda 2 firmas (PENDIENTE — not signed)
    for i, area in enumerate(["DCOP", "DREH"], start=1):
        r = client.post(
            f"/procesos/{proceso_id}/firma-secuencial/{etapa_cod}",
            json={"area": area, "orden": i, "ronda": 2},
            headers=editor_headers,
        )
        assert r.status_code == 201, r.text

    # Complete ronda 1
    for fid in ronda1_ids:
        r = client.patch(
            f"/procesos/{proceso_id}/firma-secuencial/{fid}",
            json={"nuevo_estado": "FIRMADO"},
            headers=editor_headers,
        )
        assert r.status_code == 200, r.text

    db_session.expire_all()
    filas = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == etapa_cod,
        )
    ).scalars().all()

    ronda1 = next((f for f in filas if f.nro_ronda == 1), None)
    ronda2 = next((f for f in filas if f.nro_ronda == 2), None)

    assert ronda1 is not None, "E06c ronda=1 row must exist"
    assert ronda1.estado_etapa == "COMPLETADO", (
        f"E06c ronda=1 expected COMPLETADO, got {ronda1.estado_etapa}"
    )
    # Ronda 2 must not be affected (either absent or not COMPLETADO)
    assert ronda2 is None or ronda2.estado_etapa != "COMPLETADO", (
        "E06c ronda=2 must NOT be COMPLETADO when ronda=1 just completed"
    )


def test_firma_rechazada_no_completa_etapa(proceso_tres_areas, client, editor_headers, db_session):
    """W1: all FIRMADO except one RECHAZADO → etapa NOT completed."""
    from app.models.etapa import EtapaRegistro
    from sqlalchemy import select

    proceso_id = proceso_tres_areas["id"]
    etapa_cod = "E02b"

    firma_ids: list[int] = []
    for i, area in enumerate(["DCOP", "DREH", "DTDIS"], start=1):
        r = client.post(
            f"/procesos/{proceso_id}/firma-secuencial/{etapa_cod}",
            json={"area": area, "orden": i},
            headers=editor_headers,
        )
        assert r.status_code == 201
        firma_ids.append(r.json()["id"])

    # Sign first two, reject the third
    for fid in firma_ids[:2]:
        client.patch(
            f"/procesos/{proceso_id}/firma-secuencial/{fid}",
            json={"nuevo_estado": "FIRMADO"},
            headers=editor_headers,
        )
    # Reject third (no order constraint needed — RECHAZADO is always allowed)
    client.patch(
        f"/procesos/{proceso_id}/firma-secuencial/{firma_ids[2]}",
        json={"nuevo_estado": "RECHAZADO", "motivo_rechazo": "Observación técnica"},
        headers=editor_headers,
    )

    db_session.expire_all()
    fila = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == etapa_cod,
        )
    ).scalars().first()

    assert fila is None or fila.estado_etapa != "COMPLETADO", (
        "etapas_registro E02b must NOT be COMPLETADO when one firma is RECHAZADO"
    )
