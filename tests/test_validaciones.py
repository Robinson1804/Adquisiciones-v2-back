"""Backend tests — C3b business rules R1-R8 enforcement.

One happy + one blocked test per rule (R1-R8).
Also covers: proceso-CANCELADO gate, prerequisito genérico.

Uses client+db_session from conftest.py.
autouse _clean_business_tables ensures clean state per test.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers, areas=None) -> dict:
    payload = {
        "requerimiento": "Test C3b validaciones",
        "tipo": "SERVICIO",
        "areas_usuarias": areas or ["DTDIS"],
        "anno": 2026,
        "cmn_por_area": [{"area": a, "cmn_adjunto": "SI"} for a in (areas or ["DTDIS"])],
    }
    resp = client.post("/procesos", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_e01_completado(db_session, proceso_id, areas=None):
    """Mark all E01 rows for the proceso as COMPLETADO with cmn_adjunto=SI."""
    rows = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E01",
        )
    ).scalars().all()
    for row in rows:
        row.cmn_adjunto = "SI"
        row.estado_etapa = "COMPLETADO"
    db_session.flush()


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


def _post_etapa(client, headers, proceso_id, cod, nombre="Test", **extra):
    payload = {
        "codigo_etapa": cod,
        "nombre_etapa": nombre,
        "fecha_inicio": "2026-06-01",
        "estado_etapa": "COMPLETADO",
    }
    payload.update(extra)
    return client.post(
        f"/procesos/{proceso_id}/etapas",
        json=payload,
        headers=headers,
    )


def _setup_chain_prereqs(db_session, proceso_id, stop_before_cod):
    """Insert all chain stages up to (not including) stop_before_cod as COMPLETADO.

    C3c added sequential prereqs to the main chain. Tests that register a
    mid-chain stage via the API need all prior stages present in DB.
    flujo-real-otin-v2: E01 replaced by E01a/E01b/E01c in chain.
    """
    from app.services.etapas_catalogo import CADENA
    existing = {
        row.codigo_etapa
        for row in db_session.execute(
            select(EtapaRegistro).where(EtapaRegistro.proceso_id == proceso_id)
        ).scalars().all()
    }
    for cod in CADENA:
        if cod == stop_before_cod:
            break
        if cod in existing:
            continue
        kw = {}
        if cod == "E01c":
            kw = {"area_usuaria": "DTDIS"}
        elif cod == "E08":
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
# R1 — E02 bloqueada sin E01a/E01b/E01c completadas (flujo-real-otin-v2)
# ---------------------------------------------------------------------------

def test_r1_e02_blocked_cmn_pendiente(client, editor_headers, db_session):
    """R1 BLOCKED: POST E02 when E01c is not COMPLETADO for area → 409.

    flujo-real-otin-v2: R1 is now enforced by generic prereq check on E01c.
    E01 rows no longer exist; E01c replaces them per area.
    """
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]
    # Insert E01a/E01b COMPLETADO but E01c PENDIENTE → prereq E01c not satisfied
    _insert_etapa(db_session, pid, "E01a")
    _insert_etapa(db_session, pid, "E01b")
    _insert_etapa(db_session, pid, "E01c", estado="PENDIENTE", area_usuaria="DTDIS")

    resp = _post_etapa(client, editor_headers, pid, "E02")
    assert resp.status_code == 409, resp.text
    assert "E01c" in resp.json()["detail"]


def test_r1_e02_happy_all_cmn_si(client, editor_headers, db_session):
    """R1 HAPPY: POST E02 when E01a/E01b/E01c all COMPLETADO → 201.

    flujo-real-otin-v2: E01c COMPLETADO (with cmn_siga_confirmado) satisfies prereq.
    Also need E02b before E03, but here we only test E02 registration.
    """
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]
    _insert_etapa(db_session, pid, "E01a")
    _insert_etapa(db_session, pid, "E01b")
    _insert_etapa(db_session, pid, "E01c", area_usuaria="DTDIS")

    resp = _post_etapa(client, editor_headers, pid, "E02")
    assert resp.status_code == 201, resp.text
    assert resp.json()["codigo_etapa"] == "E02"


# ---------------------------------------------------------------------------
# R2 — E10 cancelación por SIN_PRESUPUESTO
# ---------------------------------------------------------------------------

def test_r2_e10_sin_presupuesto_no_motivo(client, editor_headers, db_session):
    """R2 BLOCKED: POST E10 SIN_PRESUPUESTO without motivo_cancel → 422."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_prereqs(db_session, proc["id"], "E10")
    resp = _post_etapa(
        client, editor_headers, proc["id"], "E10",
        resultado_eval="SIN_PRESUPUESTO",
        # motivo_cancel intentionally omitted
    )
    assert resp.status_code == 422, resp.text
    assert "motivo_cancel" in resp.json()["detail"].lower()


def test_r2_e10_sin_presupuesto_with_motivo(client, editor_headers, db_session):
    """R2 HAPPY: POST E10 SIN_PRESUPUESTO with motivo_cancel → 201; proceso CANCELADO."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_prereqs(db_session, proc["id"], "E10")
    resp = _post_etapa(
        client, editor_headers, proc["id"], "E10",
        resultado_eval="SIN_PRESUPUESTO",
        motivo_cancel="Área DTDIS sin asignación presupuestal",
    )
    assert resp.status_code == 201, resp.text

    # proceso.estado must be CANCELADO
    proc_resp = client.get(f"/procesos/{proc['id']}", headers=editor_headers)
    assert proc_resp.status_code == 200, proc_resp.text
    assert proc_resp.json()["estado"] == "CANCELADO"


def test_r2_e10_validado_no_cancel(client, editor_headers, db_session):
    """R2: POST E10 VALIDADO → 201; proceso stays EN PROCESO."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_prereqs(db_session, proc["id"], "E10")
    resp = _post_etapa(
        client, editor_headers, proc["id"], "E10",
        resultado_eval="VALIDADO",
    )
    assert resp.status_code == 201, resp.text
    proc_resp = client.get(f"/procesos/{proc['id']}", headers=editor_headers)
    assert proc_resp.json()["estado"] == "EN PROCESO"


# ---------------------------------------------------------------------------
# R3 — E12 bloqueada con E11 PENDIENTE
# ---------------------------------------------------------------------------

def test_r3_e12_blocked_e11_pendiente(client, editor_headers, db_session):
    """R3 BLOCKED: POST E12 when any E11 row is PENDIENTE → 409."""
    proc = _create_proceso(client, editor_headers, areas=["DTDIS", "GOBERNANZA"])
    # E11 rows: DTDIS COMPLETADO, GOBERNANZA PENDIENTE
    _insert_etapa(
        db_session, proc["id"], "E11",
        estado="COMPLETADO", area_usuaria="DTDIS",
        monto_cert=Decimal("80000"),
    )
    _insert_etapa(
        db_session, proc["id"], "E11",
        estado="PENDIENTE", area_usuaria="GOBERNANZA",
    )
    # Also insert E11 prereq (E11 has no prereq in catalog; E12's prereq is E11)
    # E12 prereq = E11 all COMPLETADO → blocked because GOBERNANZA is PENDIENTE

    resp = _post_etapa(client, editor_headers, proc["id"], "E12")
    assert resp.status_code == 409, resp.text
    assert "E11" in resp.json()["detail"] or "pendiente" in resp.json()["detail"].lower()


def test_r3_e12_happy_all_e11_completado(client, editor_headers, db_session):
    """R3 HAPPY: POST E12 when all E11 rows COMPLETADO → 201."""
    proc = _create_proceso(client, editor_headers, areas=["DTDIS", "GOBERNANZA"])
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


# ---------------------------------------------------------------------------
# R4 — E16 alerta OTPP > 20 días (no bloquea)
# ---------------------------------------------------------------------------

def test_r4_e16_alerta_respuesta_tardia(client, editor_headers, db_session):
    """R4: GET /etapas; E16 fecha_resp_otpp - fecha_envio_otpp = 21d → alerta_otpp=True."""
    proc = _create_proceso(client, editor_headers)
    envio = date(2026, 1, 1)
    respuesta = envio + timedelta(days=21)
    _insert_etapa(
        db_session, proc["id"], "E16",
        estado="COMPLETADO",
        fecha_envio_otpp=envio,
        fecha_resp_otpp=respuesta,
    )

    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=editor_headers)
    assert resp.status_code == 200, resp.text
    etapas = resp.json()["etapas"]
    e16 = next(e for e in etapas if e["cod"] == "E16")
    assert e16["alerta_otpp"] is True


def test_r4_e16_alerta_sin_respuesta_aun(client, editor_headers, db_session):
    """R4: E16 fecha_resp_otpp=NULL, hoy - fecha_envio_otpp > 20d → alerta_otpp=True."""
    proc = _create_proceso(client, editor_headers)
    envio = date.today() - timedelta(days=21)
    _insert_etapa(
        db_session, proc["id"], "E16",
        estado="EN_CURSO",
        fecha_envio_otpp=envio,
        fecha_resp_otpp=None,
    )

    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=editor_headers)
    e16 = next(e for e in resp.json()["etapas"] if e["cod"] == "E16")
    assert e16["alerta_otpp"] is True


def test_r4_e16_no_alerta(client, editor_headers, db_session):
    """R4: E16 resp dentro del plazo → alerta_otpp=False."""
    proc = _create_proceso(client, editor_headers)
    envio = date(2026, 1, 1)
    respuesta = envio + timedelta(days=10)
    _insert_etapa(
        db_session, proc["id"], "E16",
        estado="COMPLETADO",
        fecha_envio_otpp=envio,
        fecha_resp_otpp=respuesta,
    )

    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=editor_headers)
    e16 = next(e for e in resp.json()["etapas"] if e["cod"] == "E16")
    assert e16["alerta_otpp"] is False


# ---------------------------------------------------------------------------
# R5 — E25 bloqueo + proceso CULMINADO
# ---------------------------------------------------------------------------

def test_r5_e25_blocked_e24_pendiente(client, editor_headers, db_session):
    """R5 BLOCKED: POST E25 when any E24 row PENDIENTE → 409."""
    proc = _create_proceso(client, editor_headers, areas=["DTDIS", "GOBERNANZA"])
    _insert_etapa(
        db_session, proc["id"], "E24",
        estado="COMPLETADO", area_usuaria="DTDIS",
    )
    _insert_etapa(
        db_session, proc["id"], "E24",
        estado="PENDIENTE", area_usuaria="GOBERNANZA",
    )

    resp = _post_etapa(client, editor_headers, proc["id"], "E25")
    assert resp.status_code == 409, resp.text


def test_r5_e25_happy_all_e24_completado(client, editor_headers, db_session):
    """R5 HAPPY: POST E25 all E24 COMPLETADO → 201; proceso CULMINADO."""
    proc = _create_proceso(client, editor_headers, areas=["DTDIS", "GOBERNANZA"])
    _insert_etapa(
        db_session, proc["id"], "E24",
        estado="COMPLETADO", area_usuaria="DTDIS",
    )
    _insert_etapa(
        db_session, proc["id"], "E24",
        estado="COMPLETADO", area_usuaria="GOBERNANZA",
    )

    resp = _post_etapa(client, editor_headers, proc["id"], "E25")
    assert resp.status_code == 201, resp.text

    proc_resp = client.get(f"/procesos/{proc['id']}", headers=editor_headers)
    assert proc_resp.json()["estado"] == "CULMINADO"


# ---------------------------------------------------------------------------
# R6 — E05/E06 bucles solo si E04 COMPLETADO
# ---------------------------------------------------------------------------

def test_r6_bucle_e05_blocked_no_e04(client, editor_headers):
    """R6 BLOCKED: POST /bucle E05 without E04 COMPLETADO → 409."""
    proc = _create_proceso(client, editor_headers)
    resp = client.post(
        f"/procesos/{proc['id']}/etapas/E05/bucle",
        json={"motivo_bucle": "Primera observación"},
        headers=editor_headers,
    )
    assert resp.status_code == 409, resp.text
    assert "E04" in resp.json()["detail"]


def test_r6_bucle_e05_happy_e04_done(client, editor_headers, db_session):
    """R6 HAPPY: POST /bucle E05 with E04 COMPLETADO → 201; nro_ronda increments."""
    proc = _create_proceso(client, editor_headers)
    _insert_etapa(db_session, proc["id"], "E04", estado="COMPLETADO")

    resp = client.post(
        f"/procesos/{proc['id']}/etapas/E05/bucle",
        json={"motivo_bucle": "Primera observación OEAS"},
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["nro_ronda"] == 1


# ---------------------------------------------------------------------------
# R7 — E09 solo si E08 = APROBADO
# ---------------------------------------------------------------------------

def test_r7_e09_blocked_e08_not_aprobado(client, editor_headers, db_session):
    """R7 BLOCKED: POST E09 when E08.resultado_eval != 'APROBADO' → 409."""
    proc = _create_proceso(client, editor_headers)
    _insert_etapa(
        db_session, proc["id"], "E08",
        estado="COMPLETADO", resultado_eval="CON OBSERVACIONES",
    )

    resp = _post_etapa(
        client, editor_headers, proc["id"], "E09",
        monto_cert="150000.00",
    )
    assert resp.status_code == 409, resp.text
    assert "E08" in resp.json()["detail"] or "APROBADO" in resp.json()["detail"]


def test_r7_e09_happy_e08_aprobado(client, editor_headers, db_session):
    """R7 HAPPY: POST E09 when E08.resultado_eval='APROBADO' → 201."""
    proc = _create_proceso(client, editor_headers)
    _insert_etapa(
        db_session, proc["id"], "E08",
        estado="COMPLETADO", resultado_eval="APROBADO",
    )

    resp = _post_etapa(
        client, editor_headers, proc["id"], "E09",
        monto_cert="150000.00",
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# R8 — E21 marca inicio del plazo (no bloquea)
# ---------------------------------------------------------------------------

def test_r8_e21_no_block(client, editor_headers, db_session):
    """R8: POST E21 COMPLETADO → 201; no error, fecha_inicio stored."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_prereqs(db_session, proc["id"], "E21")
    resp = _post_etapa(
        client, editor_headers, proc["id"], "E21",
        fecha_inicio="2026-07-01",
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["codigo_etapa"] == "E21"
    assert body["fecha_inicio"] == "2026-07-01"


# ---------------------------------------------------------------------------
# Proceso CANCELADO gate
# ---------------------------------------------------------------------------

def test_cancelado_proceso_blocks_all_etapas(client, editor_headers, db_session):
    """Any POST to CANCELADO proceso → 409."""
    proc = _create_proceso(client, editor_headers)
    # Cancel the proceso via E10 SIN_PRESUPUESTO (need chain prereqs E01-E09)
    _setup_chain_prereqs(db_session, proc["id"], "E10")
    _post_etapa(
        client, editor_headers, proc["id"], "E10",
        resultado_eval="SIN_PRESUPUESTO",
        motivo_cancel="Sin presupuesto test",
    )

    # Now try to register any etapa
    resp = _post_etapa(client, editor_headers, proc["id"], "E03")
    assert resp.status_code == 409, resp.text
    assert "cancelado" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Prerequisito genérico
# ---------------------------------------------------------------------------

def test_prereq_generico_e09_sin_e08_completado(client, editor_headers, db_session):
    """Prereq genérico: POST E09 without E08 COMPLETADO at all → 409."""
    proc = _create_proceso(client, editor_headers)
    # E08 exists but NOT COMPLETADO
    _insert_etapa(
        db_session, proc["id"], "E08",
        estado="PENDIENTE", resultado_eval="APROBADO",
    )

    resp = _post_etapa(
        client, editor_headers, proc["id"], "E09",
        monto_cert="150000.00",
    )
    # prereq check fires before R7 check (E08 not COMPLETADO → prereq blocked)
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# T06a — E02 blocked until all E01c COMPLETADO or NO_APLICA
# (flujo-real-otin-v2: replaces old R1 cmn_adjunto gate)
# ---------------------------------------------------------------------------

def test_e02_blocked_si_e01c_pendiente_alguna_area(client, editor_headers, db_session):
    """T06a BLOCKED: E02 returns 409 when E01c is PENDIENTE for any area_usuaria."""
    proc = _create_proceso(client, editor_headers, areas=["DCOP", "DREH"])
    # Insert prereqs: E01a COMPLETADO, E01b COMPLETADO
    _insert_etapa(db_session, proc["id"], "E01a", estado="COMPLETADO")
    _insert_etapa(db_session, proc["id"], "E01b", estado="COMPLETADO")
    # E01c DCOP = COMPLETADO, E01c DREH = PENDIENTE (blocks E02)
    _insert_etapa(db_session, proc["id"], "E01c", estado="COMPLETADO", area_usuaria="DCOP")
    _insert_etapa(db_session, proc["id"], "E01c", estado="PENDIENTE", area_usuaria="DREH")
    db_session.flush()

    resp = _post_etapa(client, editor_headers, proc["id"], "E02")
    assert resp.status_code == 409, resp.text


def test_e02_pasa_si_e01c_completado_todas_areas(client, editor_headers, db_session):
    """T06a HAPPY: E02 passes when all E01c rows are COMPLETADO."""
    proc = _create_proceso(client, editor_headers, areas=["DCOP", "DREH"])
    _insert_etapa(db_session, proc["id"], "E01a", estado="COMPLETADO")
    _insert_etapa(db_session, proc["id"], "E01b", estado="COMPLETADO")
    _insert_etapa(db_session, proc["id"], "E01c", estado="COMPLETADO", area_usuaria="DCOP")
    _insert_etapa(db_session, proc["id"], "E01c", estado="COMPLETADO", area_usuaria="DREH")
    db_session.flush()

    resp = _post_etapa(client, editor_headers, proc["id"], "E02")
    assert resp.status_code == 201, resp.text


def test_e02_pasa_si_e01c_no_aplica_una_area(client, editor_headers, db_session):
    """T06a HAPPY: E02 passes when E01c is COMPLETADO for one area and NO_APLICA for another."""
    proc = _create_proceso(client, editor_headers, areas=["DCOP", "DREH"])
    _insert_etapa(db_session, proc["id"], "E01a", estado="COMPLETADO")
    _insert_etapa(db_session, proc["id"], "E01b", estado="COMPLETADO")
    _insert_etapa(db_session, proc["id"], "E01c", estado="COMPLETADO", area_usuaria="DCOP")
    _insert_etapa(db_session, proc["id"], "E01c", estado="NO_APLICA", area_usuaria="DREH")
    db_session.flush()

    resp = _post_etapa(client, editor_headers, proc["id"], "E02")
    assert resp.status_code == 201, resp.text
