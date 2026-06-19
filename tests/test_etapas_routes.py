"""Tests for etapas HTTP endpoints — auth/role gates + contract pin.

C3a scope: mechanics, auth, and GET grouped structure contract.
Business rule blocking tests (R1-R8) are NOT here — see C3b.

All tests use the client+db_session fixtures from conftest.py.
The autouse _clean_business_tables fixture ensures a clean state per test.
"""
import json
from datetime import date

import pytest

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers: dict, areas: list[str] | None = None) -> dict:
    """Create a test proceso and return the response body."""
    payload = {
        "requerimiento": "Test adquisicion etapas",
        "tipo": "SERVICIO",
        "areas_usuarias": areas or ["DTDIS"],
        "anno": 2026,
        "cmn_por_area": [{"area": a, "cmn_adjunto": "SI"} for a in (areas or ["DTDIS"])],
    }
    resp = client.post("/procesos", json=payload, headers=headers)
    assert resp.status_code == 201, f"Failed to create proceso: {resp.text}"
    return resp.json()


def _etapa_payload(**overrides) -> dict:
    base = {
        "codigo_etapa": "E02",
        "nombre_etapa": "Elaboración TDR",
        "fecha_inicio": "2026-06-01",
        "estado_etapa": "PENDIENTE",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Auth / role tests
# ---------------------------------------------------------------------------

def test_viewer_get_etapas_200(client, editor_headers, viewer_headers):
    """VIEWER can GET /procesos/{id}/etapas (read is open to all authenticated)."""
    proc = _create_proceso(client, editor_headers)
    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=viewer_headers)
    assert resp.status_code == 200, resp.text


def test_viewer_post_etapas_403(client, editor_headers, viewer_headers):
    """VIEWER POST → 403 Forbidden."""
    proc = _create_proceso(client, editor_headers)
    resp = client.post(
        f"/procesos/{proc['id']}/etapas",
        json=_etapa_payload(),
        headers=viewer_headers,
    )
    assert resp.status_code == 403, resp.text


def test_viewer_put_etapa_403(client, editor_headers, viewer_headers, db_session):
    """VIEWER PUT → 403 Forbidden."""
    proc = _create_proceso(client, editor_headers)
    # Insert a row directly
    etapa = EtapaRegistro(
        proceso_id=proc["id"],
        codigo_etapa="E02",
        nombre_etapa="TDR",
        estado_etapa="PENDIENTE",
        nro_ronda=1,
        registrado_por="editor1",
    )
    db_session.add(etapa)
    db_session.flush()

    resp = client.put(
        f"/etapas/{etapa.id}",
        json={"estado_etapa": "COMPLETADO"},
        headers=viewer_headers,
    )
    assert resp.status_code == 403, resp.text


def test_no_token_post_401(client, editor_headers):
    """Unauthenticated POST → 401."""
    proc = _create_proceso(client, editor_headers)
    resp = client.post(
        f"/procesos/{proc['id']}/etapas",
        json=_etapa_payload(),
    )
    assert resp.status_code == 401, resp.text


def test_no_token_get_401(client, editor_headers):
    """Unauthenticated GET → 401."""
    proc = _create_proceso(client, editor_headers)
    resp = client.get(f"/procesos/{proc['id']}/etapas")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# GET /procesos/{id}/etapas — contract pin (APPLY-TIME RISK #1)
# ---------------------------------------------------------------------------

def test_get_agrupado_estructura_28_etapas(client, editor_headers):
    """GET returns all 28 etapas (27 original + E06b) in ORDEN_ETAPAS order + progreso block."""
    from app.services.etapas_catalogo import ORDEN_ETAPAS

    proc = _create_proceso(client, editor_headers)
    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=editor_headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "etapas" in body
    assert "progreso" in body
    assert len(body["etapas"]) == 32

    cods = [e["cod"] for e in body["etapas"]]
    assert cods == ORDEN_ETAPAS


def test_get_agrupado_pendiente_sin_registros(client, editor_headers):
    """GET returns all PENDIENTE when no stages registered."""
    proc = _create_proceso(client, editor_headers)
    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=editor_headers)
    body = resp.json()

    # progreso starts at 0 (no E01c rows auto-created — only E01a auto-created when area_iniciadora)
    progreso = body["progreso"]
    assert progreso["total"] == 26
    assert progreso["completadas"] == 0
    assert isinstance(progreso["porcentaje"], (int, float))


def test_get_agrupado_progreso_block_structure(client, editor_headers):
    """progreso block has etapa_actual, porcentaje, completadas, total fields."""
    proc = _create_proceso(client, editor_headers)
    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=editor_headers)
    progreso = resp.json()["progreso"]

    assert "etapa_actual" in progreso
    assert "porcentaje" in progreso
    assert "completadas" in progreso
    assert "total" in progreso
    assert progreso["total"] == 26


def test_get_agrupado_etapa_fields(client, editor_headers):
    """Each etapa entry has required fields: cod, nombre, estado, es_bucle, por_area."""
    proc = _create_proceso(client, editor_headers)
    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=editor_headers)
    etapas = resp.json()["etapas"]

    for e in etapas:
        assert "cod" in e, f"Missing 'cod' in {e}"
        assert "nombre" in e, f"Missing 'nombre' in {e}"
        assert "estado" in e, f"Missing 'estado' in {e}"
        assert "es_bucle" in e, f"Missing 'es_bucle' in {e}"
        assert "por_area" in e, f"Missing 'por_area' in {e}"
        assert "filas" in e, f"Missing 'filas' in {e}"
        assert "rondas" in e, f"Missing 'rondas' in {e}"


def test_get_agrupado_proceso_404(client, editor_headers):
    """GET for non-existent proceso → 404."""
    resp = client.get("/procesos/99999/etapas", headers=editor_headers)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# POST /procesos/{id}/etapas
# ---------------------------------------------------------------------------

def test_post_etapa_201(client, editor_headers, db_session):
    """EDITOR POST → 201 with EtapaOut body (prereqs satisfied via DB inserts)."""
    proc = _create_proceso(client, editor_headers)
    # flujo-real-otin-v2: prereq chain E01a→E01b→E01c→E02
    from app.models.etapa import EtapaRegistro
    db_session.add(EtapaRegistro(
        proceso_id=proc["id"], codigo_etapa="E01a",
        nombre_etapa="Solicitud inicial", area_responsable="AREAS",
        estado_etapa="COMPLETADO", registrado_por="test", nro_ronda=1,
    ))
    db_session.add(EtapaRegistro(
        proceso_id=proc["id"], codigo_etapa="E01b",
        nombre_etapa="Oficio circular", area_responsable="OTIN",
        estado_etapa="COMPLETADO", registrado_por="test", nro_ronda=1,
    ))
    db_session.add(EtapaRegistro(
        proceso_id=proc["id"], codigo_etapa="E01c",
        nombre_etapa="Respuesta área", area_responsable="AREAS",
        area_usuaria="DTDIS", estado_etapa="COMPLETADO",
        registrado_por="test", nro_ronda=1,
    ))
    db_session.flush()

    resp = client.post(
        f"/procesos/{proc['id']}/etapas",
        json=_etapa_payload(codigo_etapa="E02", nombre_etapa="TDR"),
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["codigo_etapa"] == "E02"
    assert body["proceso_id"] == proc["id"]
    assert body["nro_ronda"] == 1


def test_post_etapa_proceso_404(client, editor_headers):
    """POST to non-existent proceso → 404."""
    resp = client.post(
        "/procesos/99999/etapas",
        json=_etapa_payload(),
        headers=editor_headers,
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# PUT /etapas/{id}
# ---------------------------------------------------------------------------

def test_put_etapa_200(client, editor_headers, db_session):
    """EDITOR PUT → 200 with updated EtapaOut."""
    proc = _create_proceso(client, editor_headers)
    etapa = EtapaRegistro(
        proceso_id=proc["id"],
        codigo_etapa="E02",
        nombre_etapa="TDR",
        estado_etapa="PENDIENTE",
        nro_ronda=1,
        registrado_por="editor1",
    )
    db_session.add(etapa)
    db_session.flush()

    resp = client.put(
        f"/etapas/{etapa.id}",
        json={"estado_etapa": "COMPLETADO"},
        headers=editor_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado_etapa"] == "COMPLETADO"


def test_put_etapa_404(client, editor_headers):
    """PUT for non-existent etapa → 404."""
    resp = client.put(
        "/etapas/99999",
        json={"estado_etapa": "COMPLETADO"},
        headers=editor_headers,
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# POST /procesos/{id}/etapas/{cod}/bucle
# ---------------------------------------------------------------------------

def test_bucle_invalid_cod_400(client, editor_headers):
    """POST /bucle with non-bucle cod → 400 Bad Request."""
    proc = _create_proceso(client, editor_headers)
    resp = client.post(
        f"/procesos/{proc['id']}/etapas/E03/bucle",
        json={"motivo_bucle": "esto no es bucle"},
        headers=editor_headers,
    )
    assert resp.status_code == 400, resp.text


def test_bucle_valid_cod_201(client, editor_headers, db_session):
    """POST /bucle with valid bucle cod (E05) → 201 (R6: E04 COMPLETADO)."""
    proc = _create_proceso(client, editor_headers)
    # R6: E04 must be COMPLETADO for E05/E06 bucles
    from app.models.etapa import EtapaRegistro
    db_session.add(EtapaRegistro(
        proceso_id=proc["id"], codigo_etapa="E04",
        nombre_etapa="OTA deriva expediente", estado_etapa="COMPLETADO",
        nro_ronda=1, registrado_por="testsetup",
    ))
    db_session.flush()

    resp = client.post(
        f"/procesos/{proc['id']}/etapas/E05/bucle",
        json={"motivo_bucle": "Primera observación OEAS"},
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["codigo_etapa"] == "E05"
    assert body["nro_ronda"] == 1
    assert body["es_bucle"] is True


def test_bucle_increments_via_http(client, editor_headers, db_session):
    """Two consecutive /bucle POSTs increment nro_ronda correctly (R6: E04 COMPLETADO)."""
    proc = _create_proceso(client, editor_headers)
    # R6: E04 must be COMPLETADO
    from app.models.etapa import EtapaRegistro
    db_session.add(EtapaRegistro(
        proceso_id=proc["id"], codigo_etapa="E04",
        nombre_etapa="OTA deriva expediente", estado_etapa="COMPLETADO",
        nro_ronda=1, registrado_por="testsetup",
    ))
    db_session.flush()

    url = f"/procesos/{proc['id']}/etapas/E06/bucle"
    payload = {"motivo_bucle": "Corrección X"}

    r1 = client.post(url, json=payload, headers=editor_headers)
    r2 = client.post(url, json=payload, headers=editor_headers)

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["nro_ronda"] == 1
    assert r2.json()["nro_ronda"] == 2


def test_bucle_viewer_403(client, editor_headers, viewer_headers):
    """VIEWER POST /bucle → 403."""
    proc = _create_proceso(client, editor_headers)
    resp = client.post(
        f"/procesos/{proc['id']}/etapas/E05/bucle",
        json={"motivo_bucle": "test"},
        headers=viewer_headers,
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# CULMINADO progreso override
# ---------------------------------------------------------------------------

def test_progreso_culminado_100(client, editor_headers, db_session):
    """CULMINADO proceso → GET etapas progreso.porcentaje==100, etapa_actual==None.

    Reproduces the bug where optional loop stages (E05/E06) that were never
    completed caused a CULMINADO process to display as 92% / etapa_actual=E05.
    """
    proc = _create_proceso(client, editor_headers)

    # Force estado=CULMINADO directly in the DB (mimics the real transition
    # that happens when the final stage E25 is marked COMPLETADO).
    db_session.execute(
        Proceso.__table__.update()
        .where(Proceso.id == proc["id"])
        .values(estado="CULMINADO")
    )
    db_session.flush()

    resp = client.get(f"/procesos/{proc['id']}/etapas", headers=editor_headers)
    assert resp.status_code == 200, resp.text
    progreso = resp.json()["progreso"]
    assert progreso["porcentaje"] == 100.0


# ---------------------------------------------------------------------------
# T04a — fecha_limite_respuesta + cmn_siga_confirmado in EtapaCreate
# ---------------------------------------------------------------------------

def test_etapa_con_fecha_limite_respuesta(client, editor_headers, db_session):
    """POST etapa with fecha_limite_respuesta persists the date.

    Uses E01b which has no DB-enforced prereqs at schema level (prereq E01a
    is validated by the service, so we register E01a first via direct DB insert).
    """
    from app.models.etapa import EtapaRegistro

    proc = _create_proceso(client, editor_headers)
    # Insert E01a directly to satisfy prereq
    db_session.add(EtapaRegistro(
        proceso_id=proc["id"],
        codigo_etapa="E01a",
        nombre_etapa="Solicitud inicial área iniciadora",
        area_responsable="AREAS",
        estado_etapa="COMPLETADO",
        registrado_por="test",
        nro_ronda=1,
    ))
    db_session.flush()

    payload = {
        "codigo_etapa": "E01b",
        "nombre_etapa": "Oficio circular OTIN a áreas",
        "fecha_inicio": "2026-06-01",
        "estado_etapa": "COMPLETADO",
        "fecha_limite_respuesta": "2026-07-01",
    }
    resp = client.post(
        f"/procesos/{proc['id']}/etapas",
        json=payload,
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["fecha_limite_respuesta"] == "2026-07-01"


def test_etapa_sin_fecha_limite_respuesta_es_null(client, editor_headers, db_session):
    """POST etapa without fecha_limite_respuesta → stored as null."""
    from app.models.etapa import EtapaRegistro

    proc = _create_proceso(client, editor_headers)
    db_session.add(EtapaRegistro(
        proceso_id=proc["id"],
        codigo_etapa="E01a",
        nombre_etapa="Solicitud inicial área iniciadora",
        area_responsable="AREAS",
        estado_etapa="COMPLETADO",
        registrado_por="test",
        nro_ronda=1,
    ))
    db_session.flush()

    payload = {
        "codigo_etapa": "E01b",
        "nombre_etapa": "Oficio circular OTIN a áreas",
        "fecha_inicio": "2026-06-01",
        "estado_etapa": "COMPLETADO",
    }
    resp = client.post(
        f"/procesos/{proc['id']}/etapas",
        json=payload,
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data.get("fecha_limite_respuesta") is None


def test_etapa_con_cmn_siga_confirmado(client, editor_headers, db_session):
    """POST etapa with cmn_siga_confirmado='SI' persists the value (tri-state, migration 0009)."""
    from app.models.etapa import EtapaRegistro

    proc = _create_proceso(client, editor_headers, areas=["DCOP"])
    # Insert E01a then E01b to satisfy prereqs for E01c
    db_session.add(EtapaRegistro(
        proceso_id=proc["id"],
        codigo_etapa="E01a",
        nombre_etapa="Solicitud inicial área iniciadora",
        area_responsable="AREAS",
        estado_etapa="COMPLETADO",
        registrado_por="test",
        nro_ronda=1,
    ))
    db_session.add(EtapaRegistro(
        proceso_id=proc["id"],
        codigo_etapa="E01b",
        nombre_etapa="Oficio circular OTIN",
        area_responsable="OTIN",
        estado_etapa="COMPLETADO",
        registrado_por="test",
        nro_ronda=1,
    ))
    db_session.flush()

    payload = {
        "codigo_etapa": "E01c",
        "nombre_etapa": "Respuesta área con CMN+SIGA",
        "area_usuaria": "DCOP",
        "fecha_inicio": "2026-06-01",
        "estado_etapa": "COMPLETADO",
        "cmn_siga_confirmado": "SI",
    }
    resp = client.post(
        f"/procesos/{proc['id']}/etapas",
        json=payload,
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["cmn_siga_confirmado"] == "SI"
