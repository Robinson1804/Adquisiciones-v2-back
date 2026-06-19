"""Backend tests — C3b POST /procesos/{id}/reiniciar-tdr endpoint.

Tests: happy path (audit preserved + progreso recomputed), precondition checks,
       VIEWER 403, idempotency.

Uses client+db_session from conftest.py.
autouse _clean_business_tables ensures clean state per test.
"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.etapa import EtapaRegistro
from app.models.historial import HistorialCambio
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers, areas=None) -> dict:
    payload = {
        "requerimiento": "Test reiniciar TDR C3b",
        "tipo": "SERVICIO",
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
        nro_ronda=kwargs.pop("nro_ronda", 1),
        registrado_por="testsetup",
        **kwargs,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _insert_chain_prereqs_for_e10(db_session, proceso_id):
    """Insert chain stages E01a→E09 directly so E10 can be registered.

    flujo-real-otin-v2: E01 removed; chain starts with E01a/E01b/E01c.
    C3c adds sequential prereqs to the main chain. Tests that register E10 via
    the API must have E01a/E01b/E01c/E02/E02b/E03-E09 COMPLETADO in the DB first.
    """
    from sqlalchemy import select
    # Insert full chain E01a→E09 (avoid duplicates)
    existing = {
        row.codigo_etapa
        for row in db_session.execute(
            select(EtapaRegistro).where(EtapaRegistro.proceso_id == proceso_id)
        ).scalars().all()
    }
    chain_to_e09 = [
        ("E01a", {}),
        ("E01b", {}),
        ("E01c", {"area_usuaria": "DTDIS"}),
        ("E02", {}), ("E02b", {}), ("E03", {}), ("E04", {}), ("E07", {}),
        ("E08", {"resultado_eval": "APROBADO"}),
        ("E09", {"monto_cert": "1000.00"}),
    ]
    for cod, kw in chain_to_e09:
        if cod not in existing:
            _insert_etapa(db_session, proceso_id, cod, estado="COMPLETADO", **kw)
    db_session.flush()


def _cancel_proceso_via_e10(client, headers, proceso_id, motivo="Sin presupuesto",
                            db_session=None):
    """Register E10 SIN_PRESUPUESTO to cancel the proceso.

    If db_session is provided, inserts the required chain prereqs (E01-E09)
    so E10 can be registered via the API (C3c sequential chain enforcement).
    """
    if db_session is not None:
        _insert_chain_prereqs_for_e10(db_session, proceso_id)

    resp = client.post(
        f"/procesos/{proceso_id}/etapas",
        json={
            "codigo_etapa": "E10",
            "nombre_etapa": "Validación presupuesto",
            "fecha_inicio": "2026-06-01",
            "estado_etapa": "COMPLETADO",
            "resultado_eval": "SIN_PRESUPUESTO",
            "motivo_cancel": motivo,
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Failed to cancel proceso: {resp.text}"
    return resp.json()


def _get_etapas(client, headers, proceso_id) -> dict:
    resp = client.get(f"/procesos/{proceso_id}/etapas", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_reiniciar_tdr_happy(client, editor_headers, db_session):
    """CANCELADO proceso → POST reiniciar-tdr → 200; E02-E09 OMITIDO; new E02 PENDIENTE."""
    proc = _create_proceso(client, editor_headers)
    # Insert extra E02-E09 rows to have something to OMIT (chain prereqs added by _cancel_...)
    for cod in ["E05"]:
        _insert_etapa(db_session, proc["id"], cod, estado="COMPLETADO")

    _cancel_proceso_via_e10(client, editor_headers, proc["id"], db_session=db_session)

    resp = client.post(
        f"/procesos/{proc['id']}/reiniciar-tdr",
        headers=editor_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["codigo_etapa"] == "E02"
    assert body["estado_etapa"] == "PENDIENTE"
    assert body["nro_ronda"] >= 2  # incremented past the original


def test_reiniciar_tdr_e02_e09_son_omitidos(client, editor_headers, db_session):
    """After reinicio, all prior E02-E09 rows have estado='OMITIDO'."""
    proc = _create_proceso(client, editor_headers)
    # _cancel_proceso_via_e10 will insert E02-E09 chain; we verify E02/E03/E04 get omitted
    _cancel_proceso_via_e10(client, editor_headers, proc["id"], db_session=db_session)

    client.post(f"/procesos/{proc['id']}/reiniciar-tdr", headers=editor_headers)

    db_session.expire_all()
    omitidos = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proc["id"],
            EtapaRegistro.codigo_etapa.in_(["E02", "E03", "E04"]),
            EtapaRegistro.estado_etapa == "OMITIDO",
        )
    ).scalars().all()
    assert len(omitidos) == 3


def test_reiniciar_tdr_preserva_e01(client, editor_headers, db_session):
    """After reinicio, E01a/E01b/E01c rows are NOT affected — still as before.

    flujo-real-otin-v2: E01 is replaced by E01a/E01b/E01c. The reiniciar-tdr
    endpoint only OMITs E02-E09 rows, leaving E01a/E01b/E01c intact.
    """
    proc = _create_proceso(client, editor_headers)
    _cancel_proceso_via_e10(client, editor_headers, proc["id"], db_session=db_session)

    client.post(f"/procesos/{proc['id']}/reiniciar-tdr", headers=editor_headers)

    db_session.expire_all()
    # flujo-real-otin-v2: check E01a/E01b/E01c are preserved (not OMITIDO)
    for cod in ["E01a", "E01b", "E01c"]:
        rows = db_session.execute(
            select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proc["id"],
                EtapaRegistro.codigo_etapa == cod,
            )
        ).scalars().all()
        assert len(rows) > 0, f"{cod} rows missing after reinicio"
        for row in rows:
            assert row.estado_etapa != "OMITIDO", f"{cod} should NOT be OMITIDO after reinicio"


def test_reiniciar_tdr_proceso_estado_en_proceso(client, editor_headers, db_session):
    """After reinicio, proceso.estado = 'EN PROCESO' and motivo_cancel is None."""
    proc = _create_proceso(client, editor_headers)
    _cancel_proceso_via_e10(client, editor_headers, proc["id"], db_session=db_session)

    client.post(f"/procesos/{proc['id']}/reiniciar-tdr", headers=editor_headers)

    db_session.expire_all()
    proceso = db_session.get(Proceso, proc["id"])
    assert proceso.estado == "EN PROCESO"
    assert proceso.motivo_cancel is None


def test_reiniciar_tdr_preserva_historial(client, editor_headers, db_session):
    """historial_cambios has entries from before + the new reinicio entry."""
    proc = _create_proceso(client, editor_headers)
    _cancel_proceso_via_e10(client, editor_headers, proc["id"], db_session=db_session)

    client.post(f"/procesos/{proc['id']}/reiniciar-tdr", headers=editor_headers)

    db_session.expire_all()
    historial = db_session.execute(
        select(HistorialCambio).where(
            HistorialCambio.proceso_id == proc["id"],
        )
    ).scalars().all()
    # At minimum: one entry from CANCELADO transition + one from reinicio
    assert len(historial) >= 2
    campos = [h.campo_modificado for h in historial]
    assert "proceso.estado" in campos


def test_reiniciar_tdr_progreso_recalcula(client, editor_headers, db_session):
    """GET /etapas after reinicio → etapa_actual='E02', OMITIDO not counted."""
    proc = _create_proceso(client, editor_headers)
    _cancel_proceso_via_e10(client, editor_headers, proc["id"], db_session=db_session)

    client.post(f"/procesos/{proc['id']}/reiniciar-tdr", headers=editor_headers)

    etapas_data = _get_etapas(client, editor_headers, proc["id"])
    progreso = etapas_data["progreso"]
    # flujo-real-otin-v2: E01a/E01b/E01c are COMPLETADO; new E02 is PENDIENTE
    # etapa_actual should be E02 (first pending in chain after E01a/E01b/E01c)
    etapa_actual = progreso["etapa_actual"]
    assert etapa_actual == "E02", (
        f"Expected E02 as etapa_actual after reinicio, got '{etapa_actual}'"
    )


# ---------------------------------------------------------------------------
# Blocked cases (preconditions not met)
# ---------------------------------------------------------------------------

def test_reiniciar_tdr_proceso_en_proceso_409(client, editor_headers):
    """POST reiniciar-tdr on EN PROCESO proceso → 409."""
    proc = _create_proceso(client, editor_headers)
    resp = client.post(
        f"/procesos/{proc['id']}/reiniciar-tdr",
        headers=editor_headers,
    )
    assert resp.status_code == 409, resp.text
    assert "CANCELADO" in resp.json()["detail"] or "SIN_PRESUPUESTO" in resp.json()["detail"]


def test_reiniciar_tdr_cancelado_sin_e10_sin_presupuesto_409(client, editor_headers, db_session):
    """POST reiniciar-tdr on CANCELADO proceso where E10 was NOT SIN_PRESUPUESTO → 409."""
    proc = _create_proceso(client, editor_headers)
    # Manually cancel the proceso without an E10 SIN_PRESUPUESTO row
    proceso = db_session.get(Proceso, proc["id"])
    proceso.estado = "CANCELADO"
    proceso.motivo_cancel = "Cancelado manualmente"
    db_session.flush()

    resp = client.post(
        f"/procesos/{proc['id']}/reiniciar-tdr",
        headers=editor_headers,
    )
    assert resp.status_code == 409, resp.text


def test_reiniciar_tdr_viewer_403(client, editor_headers, viewer_headers, db_session):
    """VIEWER POST reiniciar-tdr → 403."""
    proc = _create_proceso(client, editor_headers)
    _cancel_proceso_via_e10(client, editor_headers, proc["id"], db_session=db_session)

    resp = client.post(
        f"/procesos/{proc['id']}/reiniciar-tdr",
        headers=viewer_headers,
    )
    assert resp.status_code == 403, resp.text


def test_reiniciar_tdr_idempotente(client, editor_headers, db_session):
    """Calling reiniciar-tdr twice: second call → 409 (proceso ya EN PROCESO)."""
    proc = _create_proceso(client, editor_headers)
    _cancel_proceso_via_e10(client, editor_headers, proc["id"], db_session=db_session)

    r1 = client.post(f"/procesos/{proc['id']}/reiniciar-tdr", headers=editor_headers)
    r2 = client.post(f"/procesos/{proc['id']}/reiniciar-tdr", headers=editor_headers)

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 409, r2.text
