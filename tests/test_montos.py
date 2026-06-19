"""Backend tests — C3b montos_proceso upsert triggers.

Tests: E09→valor_em, E12→monto_cert_total, E19→nro_ocs/monto_ocs/plazo,
       E22→fecha_inicio_srv, upsert idempotency, non-trigger stage no-op,
       vencimiento_ocs derived on GET.

Uses client+db_session from conftest.py.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.etapa import EtapaRegistro
from app.models.montos import MontosProceso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers, areas=None) -> dict:
    payload = {
        "requerimiento": "Test montos C3b",
        "tipo": "BIEN",
        "areas_usuarias": areas or ["DTDIS"],
        "anno": 2026,
        "cmn_por_area": [{"area": a, "cmn_adjunto": "SI"} for a in (areas or ["DTDIS"])],
    }
    resp = client.post("/procesos", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _insert_etapa(db_session, proceso_id, cod, estado="COMPLETADO", **kwargs):
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=f"Etapa {cod}",
        area_responsable="OTIN",
        estado_etapa=estado,
        nro_ronda=1,
        registrado_por="testsetup",
        **kwargs,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _post_etapa(client, headers, proceso_id, cod, **extra):
    payload = {
        "codigo_etapa": cod,
        "nombre_etapa": f"Etapa {cod}",
        "fecha_inicio": "2026-06-01",
        "estado_etapa": "COMPLETADO",
    }
    payload.update(extra)
    return client.post(
        f"/procesos/{proceso_id}/etapas",
        json=payload,
        headers=headers,
    )


def _get_montos(db_session, proceso_id) -> MontosProceso | None:
    return db_session.execute(
        select(MontosProceso).where(MontosProceso.proceso_id == proceso_id)
    ).scalars().first()


def _setup_chain_up_to(db_session, proceso_id, stop_before_cod):
    """Insert all chain stage rows up to (but not including) stop_before_cod.

    C3c added sequential prereqs; tests that POST a mid-chain stage via the
    API need all prior chain stages present in DB first.
    E01 rows are already created by _create_proceso (cmn_por_area); mark COMPLETADO.
    """
    from app.services.etapas_catalogo import CADENA
    existing_cods = {
        row.codigo_etapa
        for row in db_session.execute(
            select(EtapaRegistro).where(EtapaRegistro.proceso_id == proceso_id)
        ).scalars().all()
    }
    for cod in CADENA:
        if cod == stop_before_cod:
            break
        if cod == "E01":
            # Mark existing E01 rows COMPLETADO
            for row in db_session.execute(
                select(EtapaRegistro).where(
                    EtapaRegistro.proceso_id == proceso_id,
                    EtapaRegistro.codigo_etapa == "E01",
                )
            ).scalars().all():
                row.estado_etapa = "COMPLETADO"
            db_session.flush()
            continue
        if cod in existing_cods:
            continue
        kw = {}
        if cod == "E08":
            kw = {"resultado_eval": "APROBADO"}
        elif cod == "E09":
            kw = {"monto_cert": "1000.00"}
        elif cod == "E10":
            kw = {"resultado_eval": "CON_PRESUPUESTO"}
        elif cod == "E11":
            kw = {"area_usuaria": "DTDIS", "monto_cert": "500.00"}
        elif cod == "E19":
            kw = {"nro_ocs": "OCS-SETUP", "monto_ocs": "1000.00", "plazo_entrega": 30}
        elif cod == "E22":
            kw = {"fecha_inicio": "2026-01-01"}
        _insert_etapa(db_session, proceso_id, cod, estado="COMPLETADO", **kw)
    db_session.flush()


# ---------------------------------------------------------------------------
# E09 → valor_em
# ---------------------------------------------------------------------------

def test_e09_sets_valor_em(client, editor_headers, db_session):
    """POST E09 COMPLETADO with monto_cert → montos.valor_em set."""
    proc = _create_proceso(client, editor_headers)
    # R7: E08 APROBADO required
    _insert_etapa(
        db_session, proc["id"], "E08",
        estado="COMPLETADO", resultado_eval="APROBADO",
    )

    resp = _post_etapa(
        client, editor_headers, proc["id"], "E09",
        monto_cert="150000.00",
    )
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    montos = _get_montos(db_session, proc["id"])
    assert montos is not None
    assert montos.valor_em == Decimal("150000.00")


# ---------------------------------------------------------------------------
# E12 → monto_cert_total
# ---------------------------------------------------------------------------

def test_e12_sets_monto_cert_total(client, editor_headers, db_session):
    """POST E12 COMPLETADO → monto_cert_total = SUM(E11.monto_cert)."""
    proc = _create_proceso(client, editor_headers, areas=["DTDIS", "GOBERNANZA"])
    # E11 rows (both COMPLETADO to satisfy R3)
    _insert_etapa(
        db_session, proc["id"], "E11",
        estado="COMPLETADO", area_usuaria="DTDIS",
        monto_cert=Decimal("80000"),
    )
    _insert_etapa(
        db_session, proc["id"], "E11",
        estado="COMPLETADO", area_usuaria="GOBERNANZA",
        monto_cert=Decimal("70000"),
    )

    resp = _post_etapa(client, editor_headers, proc["id"], "E12")
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    montos = _get_montos(db_session, proc["id"])
    assert montos is not None
    assert montos.monto_cert_total == Decimal("150000.00")


# ---------------------------------------------------------------------------
# E19 → nro_ocs, monto_ocs, plazo_entrega
# ---------------------------------------------------------------------------

def test_e19_sets_ocs_fields(client, editor_headers, db_session):
    """POST E19 COMPLETADO → nro_ocs, monto_ocs, plazo_entrega set; montos row created."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_up_to(db_session, proc["id"], "E19")
    resp = _post_etapa(
        client, editor_headers, proc["id"], "E19",
        nro_ocs="OCS-2026-042",
        monto_ocs="148000.00",
        plazo_entrega=30,
        fecha_inicio="2026-06-01",
    )
    assert resp.status_code == 201, resp.text


def test_e19_montos_row_created(client, editor_headers, db_session):
    """POST E19 → montos_proceso row is created with correct OCS values."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_up_to(db_session, proc["id"], "E19")
    resp = _post_etapa(
        client, editor_headers, proc["id"], "E19",
        nro_ocs="OCS-2026-042",
        monto_ocs="148000.00",
        plazo_entrega=30,
        fecha_inicio="2026-06-01",
    )
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    montos = _get_montos(db_session, proc["id"])
    assert montos is not None
    assert montos.nro_ocs == "OCS-2026-042"
    assert montos.monto_ocs == Decimal("148000.00")
    assert montos.plazo_entrega == 30


# ---------------------------------------------------------------------------
# E22 → fecha_inicio_srv
# ---------------------------------------------------------------------------

def test_e22_sets_fecha_inicio_srv(client, editor_headers, db_session):
    """POST E22 COMPLETADO → montos.fecha_inicio_srv set."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_up_to(db_session, proc["id"], "E22")
    resp = _post_etapa(
        client, editor_headers, proc["id"], "E22",
        fecha_inicio="2026-06-15",
    )
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    montos = _get_montos(db_session, proc["id"])
    assert montos is not None
    assert montos.fecha_inicio_srv == date(2026, 6, 15)


# ---------------------------------------------------------------------------
# Upsert idempotency (second trigger updates, no duplicate row)
# ---------------------------------------------------------------------------

def test_montos_upsert_second_trigger(client, editor_headers, db_session):
    """POST E22 twice → only one montos row, second call updates fecha_inicio_srv."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_up_to(db_session, proc["id"], "E22")
    # First trigger
    resp1 = _post_etapa(
        client, editor_headers, proc["id"], "E22",
        fecha_inicio="2026-06-01",
    )
    assert resp1.status_code == 201, resp1.text

    # Insert a second E22 row directly and trigger via PUT to COMPLETADO
    e22_id = resp1.json()["id"]
    resp2 = client.put(
        f"/etapas/{e22_id}",
        json={"fecha_inicio": "2026-06-15"},
        headers=editor_headers,
    )
    assert resp2.status_code == 200, resp2.text

    db_session.expire_all()
    # Only one montos row
    all_montos = db_session.execute(
        select(MontosProceso).where(MontosProceso.proceso_id == proc["id"])
    ).scalars().all()
    assert len(all_montos) == 1


# ---------------------------------------------------------------------------
# Non-trigger stage → no montos row
# ---------------------------------------------------------------------------

def test_non_trigger_stage_no_montos(client, editor_headers, db_session):
    """POST E03 COMPLETADO → no montos_proceso row created."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_up_to(db_session, proc["id"], "E03")
    resp = _post_etapa(client, editor_headers, proc["id"], "E03")
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    montos = _get_montos(db_session, proc["id"])
    assert montos is None


# ---------------------------------------------------------------------------
# vencimiento_ocs derived on GET (not stored)
# ---------------------------------------------------------------------------

def test_vencimiento_ocs_derivado(client, editor_headers, db_session):
    """GET /etapas returns vencimiento_ocs = fecha_inicio + plazo_entrega for E19."""
    proc = _create_proceso(client, editor_headers)
    inicio = date(2026, 6, 1)
    _insert_etapa(
        db_session, proc["id"], "E19",
        estado="COMPLETADO",
        fecha_inicio=inicio,
        plazo_entrega=30,
        nro_ocs="OCS-2026-001",
        monto_ocs=Decimal("100000"),
    )

    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=editor_headers)
    assert resp.status_code == 200, resp.text
    etapas = resp.json()["etapas"]
    e19 = next(e for e in etapas if e["cod"] == "E19")
    assert len(e19["filas"]) == 1
    expected = (inicio + timedelta(days=30)).isoformat()
    assert e19["filas"][0]["vencimiento_ocs"] == expected


# ---------------------------------------------------------------------------
# GET /procesos/{id}/montos endpoint (C3b — ficha S4 display)
# ---------------------------------------------------------------------------

def test_get_montos_endpoint_returns_numbers(client, editor_headers, db_session):
    """GET /procesos/{id}/montos returns montos with Numeric fields as JSON numbers."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_up_to(db_session, proc["id"], "E19")
    r = _post_etapa(
        client, editor_headers, proc["id"], "E19",
        nro_ocs="OCS-2026-077", monto_ocs="200000.00", plazo_entrega=45,
    )
    assert r.status_code == 201, r.text

    resp = client.get(f"/procesos/{proc['id']}/montos", headers=editor_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nro_ocs"] == "OCS-2026-077"
    assert body["monto_ocs"] == 200000.0  # float (matches frontend number | null)
    assert body["plazo_entrega"] == 45


def test_get_montos_endpoint_null_when_empty(client, editor_headers):
    """GET /procesos/{id}/montos returns null when no trigger stage registered."""
    proc = _create_proceso(client, editor_headers)
    resp = client.get(f"/procesos/{proc['id']}/montos", headers=editor_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() is None
