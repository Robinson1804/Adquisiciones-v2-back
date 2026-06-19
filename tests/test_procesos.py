"""Backend tests for procesos endpoints — C2 spec §8 + design §5.

All tests use the transactional db_session fixture (rolls back after each test)
so the DB is clean per test. Reuses ADMIN/EDITOR/VIEWER harness from conftest.
"""
import re
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proceso_payload(**overrides) -> dict:
    """Return a minimal valid ProcesoCreate payload."""
    base = {
        "requerimiento": "Switch de red core",
        "tipo": "BIEN",
        "areas_usuarias": ["DTDIS"],
        "anno": 2026,
        "cmn_por_area": [{"area": "DTDIS", "cmn_adjunto": "SI"}],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# SC-05 / test_crear_proceso_ok
# ---------------------------------------------------------------------------

def test_crear_proceso_ok(client, editor_headers, db_session):
    """POST as EDITOR → 201; id_proceso format; estado EN PROCESO.

    flujo-real-otin-v2: NO E01 rows auto-created.
    E01a is only auto-created when fecha_solicitud is provided.
    """
    payload = _proceso_payload(areas_usuarias=["DTDIS", "GOBERNANZA"])
    resp = client.post("/procesos", json=payload, headers=editor_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert re.match(r"^\d{4}-\d{3}$", body["id_proceso"]), f"Bad format: {body['id_proceso']}"
    assert body["estado"] == "EN PROCESO"
    assert body["requerimiento"] == "Switch de red core"
    assert body["creado_por"] == "testeditor"

    # flujo-real-otin-v2: no E01 rows auto-created (old behavior removed)
    etapas_old = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == body["id"],
            EtapaRegistro.codigo_etapa == "E01",
        )
    ).scalars().all()
    assert len(etapas_old) == 0, "E01 rows should NOT be auto-created in v2 flow"


# ---------------------------------------------------------------------------
# test_id_proceso_secuencial — 3 POSTs same anno → 001, 002, 003
# ---------------------------------------------------------------------------

def test_id_proceso_secuencial(client, editor_headers):
    """Three sequential POSTs in same anno produce 001, 002, 003."""
    ids = []
    for i in range(3):
        resp = client.post(
            "/procesos",
            json=_proceso_payload(requerimiento=f"Proceso {i}", anno=2026),
            headers=editor_headers,
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id_proceso"])
    assert ids[0].endswith("-001")
    assert ids[1].endswith("-002")
    assert ids[2].endswith("-003")


# ---------------------------------------------------------------------------
# test_id_no_recicla_soft_deleted
# ---------------------------------------------------------------------------

def test_id_no_recicla_soft_deleted(client, editor_headers, admin_headers):
    """After soft-deleting 001, next create is 002 (not recycled)."""
    r1 = client.post("/procesos", json=_proceso_payload(anno=2026), headers=editor_headers)
    assert r1.status_code == 201
    id1 = r1.json()["id"]

    client.delete(f"/procesos/{id1}", headers=editor_headers)

    r2 = client.post(
        "/procesos",
        json=_proceso_payload(requerimiento="Segundo proceso", anno=2026),
        headers=editor_headers,
    )
    assert r2.status_code == 201
    assert r2.json()["id_proceso"].endswith("-002"), f"Expected -002, got {r2.json()['id_proceso']}"


# ---------------------------------------------------------------------------
# test_id_por_anno_independiente
# ---------------------------------------------------------------------------

def test_id_por_anno_independiente(client, editor_headers):
    """Sequences are independent per anno: both 2025 and 2026 start at 001."""
    r25 = client.post("/procesos", json=_proceso_payload(anno=2025), headers=editor_headers)
    r26 = client.post("/procesos", json=_proceso_payload(anno=2026), headers=editor_headers)
    assert r25.status_code == 201
    assert r26.status_code == 201
    assert r25.json()["id_proceso"].startswith("2025-")
    assert r26.json()["id_proceso"].startswith("2026-")
    assert r25.json()["id_proceso"].endswith("-001")
    assert r26.json()["id_proceso"].endswith("-001")


# ---------------------------------------------------------------------------
# test_listar_paginado
# ---------------------------------------------------------------------------

def test_listar_paginado(client, editor_headers):
    """Create 5 procesos; page_size=2 → total=5, items=2, pages=3."""
    for i in range(5):
        resp = client.post(
            "/procesos",
            json=_proceso_payload(requerimiento=f"Proceso pag {i}"),
            headers=editor_headers,
        )
        assert resp.status_code == 201

    resp = client.get("/procesos?page=1&page_size=2", headers=editor_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["pages"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2


# ---------------------------------------------------------------------------
# test_listar_filtro_estado — SC-02
# ---------------------------------------------------------------------------

def test_listar_filtro_estado(client, editor_headers):
    """Filter by estado returns only matching procesos."""
    r1 = client.post("/procesos", json=_proceso_payload(requerimiento="Proceso Uno"), headers=editor_headers)
    r2 = client.post("/procesos", json=_proceso_payload(requerimiento="Proceso Dos"), headers=editor_headers)
    assert r1.status_code == r2.status_code == 201

    id1 = r1.json()["id"]
    client.put(
        f"/procesos/{id1}",
        json={"estado": "CULMINADO"},
        headers=editor_headers,
    )

    resp = client.get("/procesos?estado=CULMINADO", headers=editor_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert all(i["estado"] == "CULMINADO" for i in body["items"])
    assert body["total"] >= 1

    resp2 = client.get("/procesos?estado=EN PROCESO", headers=editor_headers)
    assert all(i["estado"] == "EN PROCESO" for i in resp2.json()["items"])


# ---------------------------------------------------------------------------
# test_listar_filtro_search — SC-03
# ---------------------------------------------------------------------------

def test_listar_filtro_search(client, editor_headers):
    """ILIKE search on requerimiento (case-insensitive)."""
    client.post(
        "/procesos",
        json=_proceso_payload(requerimiento="Laptops Dell para DTDIS"),
        headers=editor_headers,
    )
    resp = client.get("/procesos?search=dell", headers=editor_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert any("Dell" in i["requerimiento"] or "dell" in i["requerimiento"].lower() for i in body["items"])


# ---------------------------------------------------------------------------
# test_listar_filtro_tipo
# ---------------------------------------------------------------------------

def test_listar_filtro_tipo(client, editor_headers):
    """Filter by tipo returns only matching procesos."""
    client.post("/procesos", json=_proceso_payload(tipo="BIEN"), headers=editor_headers)
    client.post("/procesos", json=_proceso_payload(tipo="SERVICIO", requerimiento="Srv"), headers=editor_headers)

    resp = client.get("/procesos?tipo=BIEN", headers=editor_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["tipo"] == "BIEN" for i in items)


# ---------------------------------------------------------------------------
# test_listar_filtro_area
# ---------------------------------------------------------------------------

def test_listar_filtro_area(client, editor_headers):
    """Filter by area returns only procesos with that area in areas_usuarias."""
    client.post(
        "/procesos",
        json=_proceso_payload(areas_usuarias=["DTDIS"]),
        headers=editor_headers,
    )
    client.post(
        "/procesos",
        json=_proceso_payload(areas_usuarias=["GOBERNANZA"], requerimiento="Solo GOBERNANZA"),
        headers=editor_headers,
    )

    resp = client.get("/procesos?area=DTDIS", headers=editor_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all("DTDIS" in i["areas_usuarias"] for i in items)
    assert all("GOBERNANZA" not in i["areas_usuarias"] for i in items)


# ---------------------------------------------------------------------------
# test_listar_excluye_eliminados
# ---------------------------------------------------------------------------

def test_listar_excluye_eliminados(client, editor_headers):
    """Soft-deleted proceso does NOT appear in list; CANCELADO proceso DOES appear."""
    r_del = client.post("/procesos", json=_proceso_payload(requerimiento="A eliminar"), headers=editor_headers)
    r_can = client.post("/procesos", json=_proceso_payload(requerimiento="A cancelar"), headers=editor_headers)
    assert r_del.status_code == r_can.status_code == 201

    id_del = r_del.json()["id"]
    id_can = r_can.json()["id"]

    # Soft delete
    client.delete(f"/procesos/{id_del}", headers=editor_headers)
    # Cancel (business state — must remain visible)
    client.put(
        f"/procesos/{id_can}",
        json={"estado": "CANCELADO", "motivo_cancel": "Sin presupuesto"},
        headers=editor_headers,
    )

    resp = client.get("/procesos", headers=editor_headers)
    ids_in_list = {i["id"] for i in resp.json()["items"]}
    assert id_del not in ids_in_list, "Soft-deleted proceso should not appear in list"
    assert id_can in ids_in_list, "CANCELADO proceso MUST appear in list (business state)"


# ---------------------------------------------------------------------------
# test_detalle_encontrado — SC-11
# ---------------------------------------------------------------------------

def test_detalle_encontrado(client, editor_headers):
    """GET /procesos/{id} returns the process ficha."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    assert r.status_code == 201
    proceso_id = r.json()["id"]

    resp = client.get(f"/procesos/{proceso_id}", headers=editor_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == proceso_id
    assert body["id_proceso"] is not None
    assert body["requerimiento"] == "Switch de red core"


# ---------------------------------------------------------------------------
# test_detalle_404_inexistente — SC-12
# ---------------------------------------------------------------------------

def test_detalle_404_inexistente(client, editor_headers):
    resp = client.get("/procesos/999999", headers=editor_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# test_detalle_404_eliminado
# ---------------------------------------------------------------------------

def test_detalle_404_eliminado(client, editor_headers):
    """GET on a soft-deleted proceso returns 404."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    proceso_id = r.json()["id"]

    client.delete(f"/procesos/{proceso_id}", headers=editor_headers)

    resp = client.get(f"/procesos/{proceso_id}", headers=editor_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# test_actualizar_ok — SC-14
# ---------------------------------------------------------------------------

def test_actualizar_ok(client, editor_headers):
    """PUT updates fields; id_proceso is unchanged."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    proceso_id = r.json()["id"]
    original_id_proceso = r.json()["id_proceso"]

    resp = client.put(
        f"/procesos/{proceso_id}",
        json={"requerimiento": "Updated text"},
        headers=editor_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["requerimiento"] == "Updated text"
    assert body["id_proceso"] == original_id_proceso


# ---------------------------------------------------------------------------
# test_actualizar_transicion_invalida — SC-15
# ---------------------------------------------------------------------------

def test_actualizar_transicion_invalida(client, editor_headers):
    """CULMINADO → EN PROCESO is an invalid transition → 422."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    proceso_id = r.json()["id"]

    # First move to CULMINADO
    client.put(f"/procesos/{proceso_id}", json={"estado": "CULMINADO"}, headers=editor_headers)

    # Try to go back — must fail
    resp = client.put(
        f"/procesos/{proceso_id}",
        json={"estado": "EN PROCESO"},
        headers=editor_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "inválida" in resp.json()["detail"].lower() or "invalida" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# test_actualizar_cancel_sin_motivo — SC-16
# ---------------------------------------------------------------------------

def test_actualizar_cancel_sin_motivo(client, editor_headers):
    """Cancel without motivo_cancel → 422."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    proceso_id = r.json()["id"]

    resp = client.put(
        f"/procesos/{proceso_id}",
        json={"estado": "CANCELADO"},
        headers=editor_headers,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# test_actualizar_cancel_con_motivo — SC-17
# ---------------------------------------------------------------------------

def test_actualizar_cancel_con_motivo(client, editor_headers):
    """Cancel with motivo_cancel → 200; estado=CANCELADO."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    proceso_id = r.json()["id"]

    resp = client.put(
        f"/procesos/{proceso_id}",
        json={"estado": "CANCELADO", "motivo_cancel": "Sin presupuesto"},
        headers=editor_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["estado"] == "CANCELADO"
    assert body["motivo_cancel"] == "Sin presupuesto"


# ---------------------------------------------------------------------------
# test_delete_soft — SC-18
# ---------------------------------------------------------------------------

def test_delete_soft(client, editor_headers, db_session):
    """DELETE sets eliminado_en; DB row still exists; estado unchanged."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    assert r.status_code == 201
    proceso_id = r.json()["id"]

    resp = client.delete(f"/procesos/{proceso_id}", headers=editor_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == proceso_id
    assert "eliminado" in body["message"].lower()

    # Row still exists in DB
    proceso = db_session.get(Proceso, proceso_id)
    assert proceso is not None, "Row must not be physically deleted"
    assert proceso.eliminado_en is not None, "eliminado_en must be set"
    assert proceso.estado == "EN PROCESO", "DELETE must not change estado"


# ---------------------------------------------------------------------------
# test_delete_404_ya_eliminado — SC-20
# ---------------------------------------------------------------------------

def test_delete_404_ya_eliminado(client, editor_headers):
    """DELETE on already soft-deleted → 404."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    proceso_id = r.json()["id"]

    client.delete(f"/procesos/{proceso_id}", headers=editor_headers)
    resp = client.delete(f"/procesos/{proceso_id}", headers=editor_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_viewer_post_403(client, viewer_headers):
    """SC-07: VIEWER POST → 403."""
    resp = client.post("/procesos", json=_proceso_payload(), headers=viewer_headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Permisos insuficientes"


def test_viewer_put_403(client, editor_headers, viewer_headers):
    """VIEWER PUT → 403."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    proceso_id = r.json()["id"]
    resp = client.put(
        f"/procesos/{proceso_id}",
        json={"requerimiento": "hack"},
        headers=viewer_headers,
    )
    assert resp.status_code == 403


def test_viewer_delete_403(client, editor_headers, viewer_headers):
    """SC-19: VIEWER DELETE → 403."""
    r = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    proceso_id = r.json()["id"]
    resp = client.delete(f"/procesos/{proceso_id}", headers=viewer_headers)
    assert resp.status_code == 403


def test_viewer_get_200(client, editor_headers, viewer_headers):
    """VIEWER GET list → 200 (reads are open to any authenticated user)."""
    client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    resp = client.get("/procesos", headers=viewer_headers)
    assert resp.status_code == 200


def test_unauth_get_401(client):
    """SC-04: No token → 401."""
    resp = client.get("/procesos")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# test_cmn_e01_correcto
# ---------------------------------------------------------------------------

def test_cmn_e01_correcto(client, editor_headers, db_session):
    """flujo-real-otin-v2: NO E01 per-area rows auto-created.
    cmn_adjunto is now stored as cmn_siga_confirmado on E01c rows (registered manually).
    This test verifies the process is created successfully without any E01/E01c auto-rows.
    """
    payload = _proceso_payload(
        areas_usuarias=["DTDIS", "GOBERNANZA", "OPERACIONES"],
        cmn_por_area=[
            {"area": "DTDIS", "cmn_adjunto": "SI"},
            {"area": "GOBERNANZA", "cmn_adjunto": "NO"},
        ],
    )
    resp = client.post("/procesos", json=payload, headers=editor_headers)
    assert resp.status_code == 201
    proceso_id = resp.json()["id"]

    # flujo-real-otin-v2: no E01 per-area rows auto-created
    etapas_e01 = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E01",
        )
    ).scalars().all()
    assert len(etapas_e01) == 0, "E01 per-area rows should NOT be auto-created in v2 flow"

    # No E01c rows either (those are registered manually)
    etapas_e01c = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E01c",
        )
    ).scalars().all()
    assert len(etapas_e01c) == 0, "E01c rows should NOT be auto-created"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

def test_areas_vacias_422(client, editor_headers):
    """SC-08: empty areas_usuarias → 422."""
    resp = client.post(
        "/procesos",
        json=_proceso_payload(areas_usuarias=[]),
        headers=editor_headers,
    )
    assert resp.status_code == 422


def test_tipo_invalido_422(client, editor_headers):
    """SC-09: invalid tipo → 422."""
    payload = _proceso_payload()
    payload["tipo"] = "OBRA"
    resp = client.post("/procesos", json=payload, headers=editor_headers)
    assert resp.status_code == 422


def test_pim_negativo_422(client, editor_headers):
    """SC-10: negative pim → 422."""
    resp = client.post(
        "/procesos",
        json=_proceso_payload(pim=-500),
        headers=editor_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# test_anno_defaults_to_current_year — W-03
# ---------------------------------------------------------------------------

def test_anno_defaults_to_current_year(client, editor_headers):
    """POST without anno field → defaults to the current calendar year."""
    payload = {
        "requerimiento": "Switch sin anno",
        "tipo": "BIEN",
        "areas_usuarias": ["DTDIS"],
        "cmn_por_area": [{"area": "DTDIS", "cmn_adjunto": "SI"}],
    }
    resp = client.post("/procesos", json=payload, headers=editor_headers)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# T03a — CMN + area_iniciadora + unidad_resp hardcoded (flujo-real-otin-v2)
# ---------------------------------------------------------------------------

def test_crear_proceso_con_cmn_y_area_iniciadora(client, editor_headers):
    """POST with denominacion_cmn + clasificador_cmn + area_iniciadora persists all."""
    payload = _proceso_payload(
        denominacion_cmn="CMN-2026-001",
        clasificador_cmn="BIEN",
        area_iniciadora="DCOP",
    )
    resp = client.post("/procesos", json=payload, headers=editor_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["denominacion_cmn"] == "CMN-2026-001"
    assert body["clasificador_cmn"] == "BIEN"
    assert body["area_iniciadora"] == "DCOP"


def test_crear_proceso_sin_cmn_retorna_nulls(client, editor_headers):
    """POST without CMN fields → 201 with denominacion_cmn=null, clasificador_cmn=null."""
    resp = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["denominacion_cmn"] is None
    assert body["clasificador_cmn"] is None
    assert body["area_iniciadora"] is None


def test_unidad_resp_hardcodeada_como_otin(client, editor_headers):
    """unidad_resp is always 'OTIN' regardless of what is passed in body."""
    # Case 1: field omitted
    r1 = client.post("/procesos", json=_proceso_payload(), headers=editor_headers)
    assert r1.status_code == 201
    assert r1.json()["unidad_resp"] == "OTIN"

    # Case 2: field provided with different value — must be overridden
    payload = _proceso_payload(unidad_resp="OTRA_UNIDAD", requerimiento="Override test")
    r2 = client.post("/procesos", json=payload, headers=editor_headers)
    assert r2.status_code == 201
    assert r2.json()["unidad_resp"] == "OTIN"
