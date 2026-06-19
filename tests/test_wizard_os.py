"""Tests for POST /procesos/{id}/registrar-orden-servicio — T08a.

Wizard: batch-register E14-E20 in one call.

TDD order:
  T08a → failing tests (RED) → this file
  T08b → endpoint implemented (GREEN)
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models.etapa import EtapaRegistro


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers, areas=None):
    resp = client.post(
        "/procesos",
        json={
            "requerimiento": "Test Wizard OS",
            "tipo": "SERVICIO",
            "areas_usuarias": areas or ["DTDIS"],
            "anno": 2026,
            "cmn_por_area": [{"area": a, "cmn_adjunto": "SI"} for a in (areas or ["DTDIS"])],
        },
        headers=headers,
    )
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


def _setup_e13_completado(db_session, proceso_id):
    """Insert E01a→E13 chain COMPLETADO so E14 wizard can be called."""
    _insert_etapa(db_session, proceso_id, "E01a")
    _insert_etapa(db_session, proceso_id, "E01b")
    _insert_etapa(db_session, proceso_id, "E01c", area_usuaria="DTDIS")
    _insert_etapa(db_session, proceso_id, "E02")
    _insert_etapa(db_session, proceso_id, "E02b")
    _insert_etapa(db_session, proceso_id, "E03")
    _insert_etapa(db_session, proceso_id, "E04")
    _insert_etapa(db_session, proceso_id, "E07")
    _insert_etapa(db_session, proceso_id, "E08", resultado_eval="APROBADO")
    _insert_etapa(db_session, proceso_id, "E09", monto_cert="1000.00")
    _insert_etapa(db_session, proceso_id, "E10", resultado_eval="CON_PRESUPUESTO")
    _insert_etapa(db_session, proceso_id, "E11", area_usuaria="DTDIS", monto_cert="500.00")
    _insert_etapa(db_session, proceso_id, "E12")
    _insert_etapa(db_session, proceso_id, "E13")
    db_session.flush()


# ---------------------------------------------------------------------------
# (a) Happy path: POST with fecha_os creates E14-E20 COMPLETADO
# ---------------------------------------------------------------------------

def test_wizard_os_creates_e14_to_e20(client, editor_headers, db_session):
    """POST /procesos/{id}/registrar-orden-servicio creates E14-E20 in one call."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]
    _setup_e13_completado(db_session, pid)

    resp = client.post(
        f"/procesos/{pid}/registrar-orden-servicio",
        json={"fecha_os": "2026-07-01"},
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    created_codes = {e["codigo_etapa"] for e in body}
    assert created_codes == {"E14", "E15", "E16", "E17", "E18", "E19", "E20"}


def test_wizard_os_e14_uses_fecha_os_by_default(client, editor_headers, db_session):
    """Without fechas_estimadas, all E14-E20 use fecha_os as fecha_inicio."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]
    _setup_e13_completado(db_session, pid)

    resp = client.post(
        f"/procesos/{pid}/registrar-orden-servicio",
        json={"fecha_os": "2026-07-01"},
        headers=editor_headers,
    )
    assert resp.status_code == 201
    for etapa in resp.json():
        assert etapa["fecha_inicio"] == "2026-07-01", (
            f"Expected fecha_inicio='2026-07-01' for {etapa['codigo_etapa']}, "
            f"got '{etapa['fecha_inicio']}'"
        )


def test_wizard_os_fechas_estimadas_override(client, editor_headers, db_session):
    """fechas_estimadas.E14 overrides fecha_os only for E14."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]
    _setup_e13_completado(db_session, pid)

    resp = client.post(
        f"/procesos/{pid}/registrar-orden-servicio",
        json={
            "fecha_os": "2026-07-01",
            "fechas_estimadas": {"E14": "2026-07-15"},
        },
        headers=editor_headers,
    )
    assert resp.status_code == 201
    by_cod = {e["codigo_etapa"]: e for e in resp.json()}
    assert by_cod["E14"]["fecha_inicio"] == "2026-07-15"
    assert by_cod["E15"]["fecha_inicio"] == "2026-07-01"


# ---------------------------------------------------------------------------
# (d) E13 not COMPLETADO → 422
# ---------------------------------------------------------------------------

def test_wizard_os_blocked_without_e13(client, editor_headers, db_session):
    """POST blocked with 422 when E13 is not COMPLETADO."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]
    # Do NOT insert E13

    resp = client.post(
        f"/procesos/{pid}/registrar-orden-servicio",
        json={"fecha_os": "2026-07-01"},
        headers=editor_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "E13" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# (e) E19 already registered → 409
# ---------------------------------------------------------------------------

def test_wizard_os_blocked_if_e19_exists(client, editor_headers, db_session):
    """POST blocked with 409 when E19 already exists."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]
    _setup_e13_completado(db_session, pid)
    # Insert E19 (e.g. registered manually)
    _insert_etapa(
        db_session, pid, "E19",
        nro_ocs="OCS-001", monto_ocs="1000.00", plazo_entrega=30
    )

    resp = client.post(
        f"/procesos/{pid}/registrar-orden-servicio",
        json={"fecha_os": "2026-07-01"},
        headers=editor_headers,
    )
    assert resp.status_code == 409, resp.text
    assert "E19" in resp.json()["detail"]
