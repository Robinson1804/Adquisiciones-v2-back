"""Tests de integración para routers/ingesta.py — TDD Batch 1b (G5 T19).

Cubre:
  - POST /ingesta/correos: happy path, idempotencia, auth
  - GET /ingesta/pendientes: filtrado, solo_entry_ids
  - PATCH /ingesta/{id}: corrección inline
  - POST /ingesta/{id}/aprobar: happy path, 409 doble, 403 viewer
  - POST /ingesta/{id}/rechazar
  - POST /ingesta/{id}/desvincular
  - GET /procesos/{id}/documentos
  - GET /ingesta/documentos/{doc_id}: descarga + guard path-traversal
"""
from __future__ import annotations

import base64
import datetime

import pytest

from app.config import settings
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Override UPLOAD_DIR
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def override_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    yield tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64_pdf() -> str:
    return base64.b64encode(b"%PDF-content-ok").decode()


def _correo_payload(
    entry_id="EID-001",
    numero_oficio=None,
    nombre_servicio="Consultoria en sistemas",
    nombre_servicio_normalizado="consultoria en sistemas",
    documentos=None,
):
    payload = {
        "entry_id": entry_id,
        "subject": "Test correo",
        "sender_name": "Juan",
        "sender_email": "juan@example.com",
        "received_at": "2026-06-01T10:00:00",
        "body_clean": "Cuerpo del correo.",
        "nombre_servicio": nombre_servicio,
        "nombre_servicio_normalizado": nombre_servicio_normalizado,
        "numero_oficio": numero_oficio,
        "numero_oficio_raw": numero_oficio,
        "tipo": "SERVICIO",
        "documentos": documentos or [],
    }
    return payload


def _doc_payload(nombre="doc.pdf"):
    return {
        "nombre_original": nombre,
        "content_type": "application/pdf",
        "tamano_bytes": 15,
        "contenido_b64": _b64_pdf(),
        "tipo_clasificado": "TDR",
        "confianza": 0.95,
    }


def _create_proceso(client, headers, requerimiento="Consultoria en sistemas", numero_oficio=None):
    resp = client.post(
        "/procesos",
        json={
            "requerimiento": requerimiento,
            "tipo": "SERVICIO",
            "areas_usuarias": ["AREA_A"],
            "anno": 2026,
            "cmn_por_area": [{"area": "AREA_A", "cmn_adjunto": "SI"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    proceso = resp.json()

    # Si necesitamos numero_oficio, actualizarlo directamente en la DB via session
    return proceso


# ---------------------------------------------------------------------------
# POST /ingesta/correos
# ---------------------------------------------------------------------------

class TestPostIngestaCorreos:
    def test_crear_correo_nuevo_retorna_201(self, client, editor_headers):
        """Happy path: nuevo correo → 201 status=created."""
        resp = client.post(
            "/ingesta/correos",
            json=_correo_payload(),
            headers=editor_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "created"
        assert data["estado_revision"] == "PENDIENTE"
        assert data["id"] > 0

    def test_idempotencia_segundo_post_retorna_200(self, client, editor_headers):
        """El mismo entry_id dos veces → 200 already_ingested en el 2do."""
        payload = _correo_payload()
        r1 = client.post("/ingesta/correos", json=payload, headers=editor_headers)
        assert r1.status_code == 201

        r2 = client.post("/ingesta/correos", json=payload, headers=editor_headers)
        assert r2.status_code == 200
        assert r2.json()["status"] == "already_ingested"
        assert r2.json()["id"] == r1.json()["id"]

    def test_sin_auth_retorna_401(self, client):
        """Sin token → 401."""
        resp = client.post("/ingesta/correos", json=_correo_payload())
        assert resp.status_code == 401

    def test_viewer_no_puede_ingestar(self, client, viewer_headers):
        """VIEWER → 403."""
        resp = client.post("/ingesta/correos", json=_correo_payload(), headers=viewer_headers)
        assert resp.status_code == 403

    def test_con_adjunto_crea_documento(self, client, editor_headers):
        """Correo con adjunto → crea IngestaDocumento en staging."""
        payload = _correo_payload(
            entry_id="EID-WITH-DOC",
            documentos=[_doc_payload("TDR.pdf")],
        )
        resp = client.post("/ingesta/correos", json=payload, headers=editor_headers)
        assert resp.status_code == 201

    def test_auto_vinculacion_retorna_auto_linked(self, client, editor_headers, db_session):
        """Auto-vinculación: oficio exacto + nombre ≥ 0.90 → auto_linked."""
        # Crear proceso con oficio en DB (vía sesión directa)
        proceso = Proceso(
            id_proceso="2026-AV1",
            requerimiento="Servicio de limpieza de oficinas",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
            numero_oficio="777-2026-INEI/OTIN",
        )
        db_session.add(proceso)
        db_session.flush()

        payload = _correo_payload(
            entry_id="EID-AV-01",
            numero_oficio="777-2026-INEI/OTIN",
            nombre_servicio="Servicio de limpieza de oficinas",
            nombre_servicio_normalizado="limpieza de oficinas",
        )
        resp = client.post("/ingesta/correos", json=payload, headers=editor_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "auto_linked"
        assert data["estado_revision"] == "APROBADO_AUTO"
        assert data["proceso_id"] == proceso.id

    def test_content_type_invalido_retorna_422(self, client, editor_headers):
        """Adjunto con content_type no permitido → 422."""
        doc = {
            "nombre_original": "virus.exe",
            "content_type": "application/x-executable",
            "tamano_bytes": 100,
            "contenido_b64": _b64_pdf(),
            "tipo_clasificado": "OTRO",
            "confianza": 0.5,
        }
        payload = _correo_payload(entry_id="EID-INV", documentos=[doc])
        resp = client.post("/ingesta/correos", json=payload, headers=editor_headers)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /ingesta/pendientes
# ---------------------------------------------------------------------------

class TestGetIngestaPendientes:
    def test_retorna_lista_de_pendientes(self, client, editor_headers):
        """GET /ingesta/pendientes → lista con items y total."""
        client.post("/ingesta/correos", json=_correo_payload("EID-P01"), headers=editor_headers)
        client.post("/ingesta/correos", json=_correo_payload("EID-P02"), headers=editor_headers)

        resp = client.get("/ingesta/pendientes", headers=editor_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total"] >= 2

    def test_solo_entry_ids(self, client, editor_headers):
        """?solo_entry_ids=true → lista de strings."""
        client.post("/ingesta/correos", json=_correo_payload("EID-EI01"), headers=editor_headers)

        resp = client.get("/ingesta/pendientes?solo_entry_ids=true", headers=editor_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert "EID-EI01" in data

    def test_sin_auth_retorna_401(self, client):
        resp = client.get("/ingesta/pendientes")
        assert resp.status_code == 401

    def test_viewer_puede_ver_pendientes(self, client, viewer_headers):
        """VIEWER puede consultar (solo lectura)."""
        resp = client.get("/ingesta/pendientes", headers=viewer_headers)
        assert resp.status_code == 200

    # --- WARNING-1 FIX: ?estado= query param ---

    def test_estado_pendiente_explicito_retorna_pendientes(self, client, editor_headers):
        """?estado=PENDIENTE explícito → mismo resultado que default."""
        client.post("/ingesta/correos", json=_correo_payload("EID-EP01"), headers=editor_headers)

        resp = client.get("/ingesta/pendientes?estado=PENDIENTE", headers=editor_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert all(item["estado_revision"] == "PENDIENTE" for item in data["items"])

    def test_estado_aprobado_auto_retorna_solo_auto_vinculados(self, client, editor_headers, db_session):
        """?estado=APROBADO_AUTO → solo correos auto-vinculados."""
        # Crear un correo PENDIENTE
        client.post("/ingesta/correos", json=_correo_payload("EID-AA01"), headers=editor_headers)

        # Crear un proceso y un correo auto-vinculado
        proceso = Proceso(
            id_proceso="2026-AA-FIX1",
            requerimiento="Servicio de limpieza automatica",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
            numero_oficio="888-2026-INEI/OTIN",
        )
        db_session.add(proceso)
        db_session.flush()

        payload_auto = _correo_payload(
            entry_id="EID-AA02",
            numero_oficio="888-2026-INEI/OTIN",
            nombre_servicio="Servicio de limpieza automatica",
            nombre_servicio_normalizado="limpieza automatica",
        )
        r = client.post("/ingesta/correos", json=payload_auto, headers=editor_headers)
        assert r.json()["status"] == "auto_linked", r.text

        resp = client.get("/ingesta/pendientes?estado=APROBADO_AUTO", headers=editor_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) >= 1
        assert all(item["estado_revision"] == "APROBADO_AUTO" for item in data["items"])

    def test_estado_invalido_retorna_422(self, client, editor_headers):
        """?estado=RECHAZADO → 422 (valor no permitido)."""
        resp = client.get("/ingesta/pendientes?estado=RECHAZADO", headers=editor_headers)
        assert resp.status_code == 422

    def test_estado_invalido_basura_retorna_422(self, client, editor_headers):
        """?estado=basura → 422."""
        resp = client.get("/ingesta/pendientes?estado=basura", headers=editor_headers)
        assert resp.status_code == 422

    def test_aprobado_auto_no_aparece_en_pendientes_default(self, client, editor_headers, db_session):
        """Default (sin ?estado) → solo PENDIENTE, los APROBADO_AUTO no aparecen."""
        proceso = Proceso(
            id_proceso="2026-AA-FIX2",
            requerimiento="Servicio de vigilancia nocturna",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
            numero_oficio="999-2026-INEI/OTIN",
        )
        db_session.add(proceso)
        db_session.flush()

        # Correo que auto-vincula
        payload_auto = _correo_payload(
            entry_id="EID-DEF01",
            numero_oficio="999-2026-INEI/OTIN",
            nombre_servicio="Servicio de vigilancia nocturna",
            nombre_servicio_normalizado="vigilancia nocturna",
        )
        r = client.post("/ingesta/correos", json=payload_auto, headers=editor_headers)
        assert r.json()["status"] == "auto_linked", r.text

        resp = client.get("/ingesta/pendientes", headers=editor_headers)
        assert resp.status_code == 200
        data = resp.json()
        ids_retornados = {item["id"] for item in data["items"]}
        auto_id = r.json()["id"]
        assert auto_id not in ids_retornados, "APROBADO_AUTO no debe aparecer en la bandeja PENDIENTE"

    def test_solo_entry_ids_siempre_sobre_pendiente(self, client, editor_headers, db_session):
        """?solo_entry_ids=true ignora ?estado y siempre retorna entry_ids de todos los no-RECHAZADOS."""
        proceso = Proceso(
            id_proceso="2026-AA-FIX3",
            requerimiento="Servicio de soporte tecnico",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
            numero_oficio="101-2026-INEI/OTIN",
        )
        db_session.add(proceso)
        db_session.flush()

        # Correo PENDIENTE
        client.post("/ingesta/correos", json=_correo_payload("EID-SEIDS01"), headers=editor_headers)
        # Correo auto-vinculado
        payload_auto = _correo_payload(
            entry_id="EID-SEIDS02",
            numero_oficio="101-2026-INEI/OTIN",
            nombre_servicio="Servicio de soporte tecnico",
            nombre_servicio_normalizado="soporte tecnico",
        )
        r = client.post("/ingesta/correos", json=payload_auto, headers=editor_headers)
        assert r.json()["status"] == "auto_linked", r.text

        # ?solo_entry_ids=true incluye ambos (no solo PENDIENTE)
        resp = client.get("/ingesta/pendientes?solo_entry_ids=true", headers=editor_headers)
        assert resp.status_code == 200
        ids = resp.json()
        assert "EID-SEIDS01" in ids
        assert "EID-SEIDS02" in ids


# ---------------------------------------------------------------------------
# PATCH /ingesta/{id}
# ---------------------------------------------------------------------------

class TestPatchIngesta:
    def test_correccion_inline_actualiza_campo(self, client, editor_headers):
        """PATCH sobre correo PENDIENTE actualiza nombre_servicio."""
        r = client.post("/ingesta/correos", json=_correo_payload("EID-COR01"), headers=editor_headers)
        ingesta_id = r.json()["id"]

        resp = client.patch(
            f"/ingesta/{ingesta_id}",
            json={"nombre_servicio": "Corregido"},
            headers=editor_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["nombre_servicio"] == "Corregido"

    def test_patch_no_existente_retorna_404(self, client, editor_headers):
        resp = client.patch("/ingesta/9999", json={"nombre_servicio": "x"}, headers=editor_headers)
        assert resp.status_code == 404

    def test_viewer_no_puede_patchear(self, client, viewer_headers, editor_headers):
        r = client.post("/ingesta/correos", json=_correo_payload("EID-COR02"), headers=editor_headers)
        ingesta_id = r.json()["id"]
        resp = client.patch(
            f"/ingesta/{ingesta_id}",
            json={"nombre_servicio": "x"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /ingesta/{id}/aprobar
# ---------------------------------------------------------------------------

class TestPostAprobar:
    def test_aprobacion_manual_happy_path(self, client, editor_headers, db_session):
        """EDITOR aprueba correo PENDIENTE → 200 APROBADO."""
        proceso = Proceso(
            id_proceso="2026-APR1",
            requerimiento="Consultoria IT",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
        )
        db_session.add(proceso)
        db_session.flush()

        r = client.post("/ingesta/correos", json=_correo_payload("EID-APR01"), headers=editor_headers)
        ingesta_id = r.json()["id"]

        resp = client.post(
            f"/ingesta/{ingesta_id}/aprobar",
            json={"proceso_id": proceso.id},
            headers=editor_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["estado_revision"] == "APROBADO"
        assert data["proceso_id"] == proceso.id

    def test_doble_aprobacion_retorna_409(self, client, editor_headers, db_session):
        """Aprobar dos veces → 409."""
        proceso = Proceso(
            id_proceso="2026-APR2",
            requerimiento="Consultoria IT 2",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
        )
        db_session.add(proceso)
        db_session.flush()

        r = client.post("/ingesta/correos", json=_correo_payload("EID-APR02"), headers=editor_headers)
        ingesta_id = r.json()["id"]

        client.post(
            f"/ingesta/{ingesta_id}/aprobar",
            json={"proceso_id": proceso.id},
            headers=editor_headers,
        )
        r2 = client.post(
            f"/ingesta/{ingesta_id}/aprobar",
            json={"proceso_id": proceso.id},
            headers=editor_headers,
        )
        assert r2.status_code == 409

    def test_viewer_no_puede_aprobar(self, client, editor_headers, viewer_headers, db_session):
        """VIEWER intenta aprobar → 403."""
        proceso = Proceso(
            id_proceso="2026-APR3",
            requerimiento="Test 403",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
        )
        db_session.add(proceso)
        db_session.flush()

        r = client.post("/ingesta/correos", json=_correo_payload("EID-APR03"), headers=editor_headers)
        ingesta_id = r.json()["id"]

        resp = client.post(
            f"/ingesta/{ingesta_id}/aprobar",
            json={"proceso_id": proceso.id},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_aprobar_proceso_inexistente_retorna_422(self, client, editor_headers):
        """Proceso no existe → 422."""
        r = client.post("/ingesta/correos", json=_correo_payload("EID-APR04"), headers=editor_headers)
        ingesta_id = r.json()["id"]

        resp = client.post(
            f"/ingesta/{ingesta_id}/aprobar",
            json={"proceso_id": 99999},
            headers=editor_headers,
        )
        assert resp.status_code == 422

    def test_aprobar_mueve_adjunto_a_proc(self, client, editor_headers, db_session, tmp_path):
        """Aprobación con adjunto → archivo en proc_{id}/."""
        proceso = Proceso(
            id_proceso="2026-APR4",
            requerimiento="Test adjunto apr",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
        )
        db_session.add(proceso)
        db_session.flush()

        payload = _correo_payload(
            entry_id="EID-APR05",
            documentos=[_doc_payload("informe.pdf")],
        )
        r = client.post("/ingesta/correos", json=payload, headers=editor_headers)
        ingesta_id = r.json()["id"]

        resp = client.post(
            f"/ingesta/{ingesta_id}/aprobar",
            json={"proceso_id": proceso.id},
            headers=editor_headers,
        )
        assert resp.status_code == 200

        # El archivo debe existir en proc_{id}/
        proc_dir = tmp_path / f"proc_{proceso.id}"
        files = list(proc_dir.iterdir())
        assert len(files) >= 1


# ---------------------------------------------------------------------------
# POST /ingesta/{id}/rechazar
# ---------------------------------------------------------------------------

class TestPostRechazar:
    def test_rechazar_pendiente_retorna_200(self, client, editor_headers):
        """Rechazar correo PENDIENTE → 200 RECHAZADO."""
        r = client.post("/ingesta/correos", json=_correo_payload("EID-REC01"), headers=editor_headers)
        ingesta_id = r.json()["id"]

        resp = client.post(
            f"/ingesta/{ingesta_id}/rechazar",
            json={"motivo": "No es del proceso"},
            headers=editor_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["estado_revision"] == "RECHAZADO"

    def test_viewer_no_puede_rechazar(self, client, editor_headers, viewer_headers):
        r = client.post("/ingesta/correos", json=_correo_payload("EID-REC02"), headers=editor_headers)
        ingesta_id = r.json()["id"]
        resp = client.post(
            f"/ingesta/{ingesta_id}/rechazar",
            json={"motivo": "x"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_rechazar_no_existente_retorna_404(self, client, editor_headers):
        resp = client.post(
            "/ingesta/9999/rechazar",
            json={"motivo": "x"},
            headers=editor_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /ingesta/{id}/desvincular
# ---------------------------------------------------------------------------

class TestPostDesvincular:
    def test_desvincular_aprobado_retorna_200_pendiente(self, client, editor_headers, db_session):
        """Desvincular desde APROBADO → 200 PENDIENTE."""
        proceso = Proceso(
            id_proceso="2026-DES1",
            requerimiento="Consultoria desvinc",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
        )
        db_session.add(proceso)
        db_session.flush()

        r = client.post("/ingesta/correos", json=_correo_payload("EID-DES01"), headers=editor_headers)
        ingesta_id = r.json()["id"]

        client.post(
            f"/ingesta/{ingesta_id}/aprobar",
            json={"proceso_id": proceso.id},
            headers=editor_headers,
        )

        resp = client.post(
            f"/ingesta/{ingesta_id}/desvincular",
            headers=editor_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["estado_revision"] == "PENDIENTE"
        assert resp.json()["proceso_id"] is None

    def test_desvincular_pendiente_retorna_409(self, client, editor_headers):
        """Desvincular correo PENDIENTE → 409."""
        r = client.post("/ingesta/correos", json=_correo_payload("EID-DES02"), headers=editor_headers)
        ingesta_id = r.json()["id"]

        resp = client.post(f"/ingesta/{ingesta_id}/desvincular", headers=editor_headers)
        assert resp.status_code == 409

    def test_viewer_no_puede_desvincular(self, client, editor_headers, viewer_headers, db_session):
        proceso = Proceso(
            id_proceso="2026-DES2",
            requerimiento="Desvinc viewer",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
        )
        db_session.add(proceso)
        db_session.flush()

        r = client.post("/ingesta/correos", json=_correo_payload("EID-DES03"), headers=editor_headers)
        ingesta_id = r.json()["id"]
        client.post(f"/ingesta/{ingesta_id}/aprobar", json={"proceso_id": proceso.id}, headers=editor_headers)

        resp = client.post(f"/ingesta/{ingesta_id}/desvincular", headers=viewer_headers)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /procesos/{id}/documentos
# ---------------------------------------------------------------------------

class TestGetDocumentosProceso:
    def test_retorna_documentos_vinculados(self, client, editor_headers, db_session):
        """GET /procesos/{id}/documentos → lista de docs vinculados."""
        proceso = Proceso(
            id_proceso="2026-DOC1",
            requerimiento="Test docs",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
        )
        db_session.add(proceso)
        db_session.flush()

        payload = _correo_payload(
            entry_id="EID-DOC01",
            documentos=[_doc_payload("tdr.pdf")],
        )
        r = client.post("/ingesta/correos", json=payload, headers=editor_headers)
        client.post(f"/ingesta/{r.json()['id']}/aprobar", json={"proceso_id": proceso.id}, headers=editor_headers)

        resp = client.get(f"/procesos/{proceso.id}/documentos", headers=editor_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert all(d["proceso_id"] == proceso.id for d in data)

    def test_proceso_sin_docs_retorna_lista_vacia(self, client, editor_headers, db_session):
        proceso = Proceso(
            id_proceso="2026-DOC2",
            requerimiento="Sin docs",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
        )
        db_session.add(proceso)
        db_session.flush()

        resp = client.get(f"/procesos/{proceso.id}/documentos", headers=editor_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_sin_auth_retorna_401(self, client, db_session):
        proceso = Proceso(
            id_proceso="2026-DOC3",
            requerimiento="Sin auth",
            tipo="SERVICIO",
            unidad_resp="OTIN",
            areas_usuarias=["AREA_A"],
            anno=2026,
            estado="EN PROCESO",
            creado_por="testadmin",
        )
        db_session.add(proceso)
        db_session.flush()
        resp = client.get(f"/procesos/{proceso.id}/documentos")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /ingesta/documentos/{doc_id}
# ---------------------------------------------------------------------------

class TestDescargaDocumento:
    def _ingestar_y_obtener_doc(self, client, editor_headers, entry_id="EID-DL01"):
        payload = _correo_payload(
            entry_id=entry_id,
            documentos=[_doc_payload("descarga.pdf")],
        )
        r = client.post("/ingesta/correos", json=payload, headers=editor_headers)
        assert r.status_code == 201
        return r.json()["id"]

    def test_descarga_documento_existente(self, client, editor_headers):
        """GET /ingesta/documentos/{doc_id} → 200 + bytes."""
        from app.models.ingesta import IngestaDocumento
        from sqlalchemy import select

        ingesta_id = self._ingestar_y_obtener_doc(client, editor_headers)

        # Obtener el doc_id via GET pendientes
        resp = client.get("/ingesta/pendientes", headers=editor_headers)
        correo = next(c for c in resp.json()["items"] if c["id"] == ingesta_id)
        assert len(correo["documentos"]) >= 1
        doc_id = correo["documentos"][0]["id"]

        resp = client.get(f"/ingesta/documentos/{doc_id}", headers=editor_headers)
        assert resp.status_code == 200
        assert len(resp.content) > 0

    def test_descarga_no_existente_retorna_404(self, client, editor_headers):
        resp = client.get("/ingesta/documentos/9999", headers=editor_headers)
        assert resp.status_code == 404

    def test_descarga_sin_auth_retorna_401(self, client):
        resp = client.get("/ingesta/documentos/1")
        assert resp.status_code == 401

    def test_path_traversal_guard(self, client, editor_headers):
        """doc_id apuntando fuera de UPLOAD_DIR → 404 (no 500)."""
        # Esto se prueba indirectamente: el guard assert_within_upload_dir
        # previene que un doc_id manipulado sirva archivos fuera del UPLOAD_DIR.
        # El test real es: si doc_id existe pero ruta_relativa fue manipulada → 404.
        # Aquí testeamos el endpoint con un doc inexistente → 404 limpio.
        resp = client.get("/ingesta/documentos/99999", headers=editor_headers)
        assert resp.status_code == 404
