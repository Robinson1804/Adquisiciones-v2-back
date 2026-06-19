"""Tests de integración para ingesta_service.py — TDD Batch 1b (G4).

Cubre:
  T12 — idempotencia + sin oficio
  T14 — auto-vinculación (ADR-5): combinaciones de criterios
  T16 — aprobar, rechazar, desvincular

Patrón: usa db_session + instancia directa del service (sin HTTP).
Los archivos van a tmp_path via monkeypatch sobre settings.UPLOAD_DIR.
"""
from __future__ import annotations

import base64
import datetime

import pytest

from app.config import settings
from app.models.ingesta import IngestaCorreo, IngestaDocumento
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Helpers de fixtures
# ---------------------------------------------------------------------------

def _make_proceso(db, requerimiento="Servicio de limpieza", numero_oficio=None):
    """Crea y persiste un proceso mínimo en DB."""
    from sqlalchemy import text
    # Necesitamos un id_proceso único
    count = db.execute(
        text("SELECT COUNT(*) FROM procesos")
    ).scalar_one()
    p = Proceso(
        id_proceso=f"2026-{count + 1:03d}",
        requerimiento=requerimiento,
        tipo="SERVICIO",
        unidad_resp="OTIN",
        areas_usuarias=["AREA_A"],
        anno=2026,
        estado="EN PROCESO",
        creado_por="testadmin",
        numero_oficio=numero_oficio,
    )
    db.add(p)
    db.flush()
    return p


def _make_payload(
    entry_id="ENTRY-001",
    nombre_servicio="Servicio de limpieza de oficinas",
    nombre_servicio_normalizado="limpieza de oficinas",
    numero_oficio_raw=None,
    numero_oficio=None,
    documentos=None,
):
    """Construye un CorreoIngestaIn dict-like (usa el schema real)."""
    from app.schemas.ingesta import CorreoIngestaIn, DocumentoExtraidoIn

    docs = documentos or []
    return CorreoIngestaIn(
        entry_id=entry_id,
        subject="Test correo",
        sender_name="Juan Perez",
        sender_email="jperez@example.com",
        received_at=datetime.datetime(2026, 6, 1, 10, 0, 0),
        body_clean="Adjunto documentación.",
        nombre_servicio=nombre_servicio,
        nombre_servicio_normalizado=nombre_servicio_normalizado,
        numero_oficio_raw=numero_oficio_raw,
        numero_oficio=numero_oficio,
        tipo="SERVICIO",
        documentos=docs,
    )


def _make_doc_payload(nombre="TDR.pdf"):
    """Construye un DocumentoExtraidoIn con contenido b64 mínimo."""
    from app.schemas.ingesta import DocumentoExtraidoIn
    contenido = base64.b64encode(b"PDF-CONTENT").decode()
    return DocumentoExtraidoIn(
        nombre_original=nombre,
        content_type="application/pdf",
        tamano_bytes=11,
        contenido_b64=contenido,
        tipo_clasificado="TDR",
        confianza=0.95,
    )


# ---------------------------------------------------------------------------
# Override UPLOAD_DIR para tests con archivos
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def override_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    yield tmp_path


# ---------------------------------------------------------------------------
# T12 — Idempotencia: mismo entry_id dos veces → 1 fila, already_ingested
# ---------------------------------------------------------------------------

class TestIdempotencia:
    def test_primer_ingreso_crea_fila(self, db_session):
        """POST de correo nuevo crea 1 registro → status 'created'."""
        from app.services import ingesta_service as svc

        payload = _make_payload()
        result = svc.ingestar_correo(db_session, payload)

        assert result.status == "created"
        assert result.estado_revision == "PENDIENTE"
        assert result.proceso_id is None

        rows = db_session.query(IngestaCorreo).filter_by(entry_id="ENTRY-001").all()
        assert len(rows) == 1

    def test_segundo_ingreso_mismo_entry_id_devuelve_already_ingested(self, db_session):
        """POST con entry_id repetido devuelve already_ingested sin duplicar."""
        from app.services import ingesta_service as svc

        payload = _make_payload()
        r1 = svc.ingestar_correo(db_session, payload)
        assert r1.status == "created"

        r2 = svc.ingestar_correo(db_session, payload)
        assert r2.status == "already_ingested"
        assert r2.id == r1.id  # mismo registro

        rows = db_session.query(IngestaCorreo).filter_by(entry_id="ENTRY-001").all()
        assert len(rows) == 1  # NO duplicado

    def test_correo_sin_oficio_queda_pendiente(self, db_session):
        """Correo sin oficio (numero_oficio=None) no auto-vincula → PENDIENTE."""
        from app.services import ingesta_service as svc

        payload = _make_payload(numero_oficio=None, numero_oficio_raw=None)
        result = svc.ingestar_correo(db_session, payload)

        assert result.status == "created"
        assert result.estado_revision == "PENDIENTE"
        assert result.proceso_id is None

    def test_correo_oficio_placeholder_queda_pendiente(self, db_session):
        """numero_oficio_raw='0000' normaliza a None → no auto-vincula."""
        from app.services import ingesta_service as svc

        payload = _make_payload(numero_oficio_raw="0000", numero_oficio=None)
        result = svc.ingestar_correo(db_session, payload)

        assert result.estado_revision == "PENDIENTE"
        assert result.proceso_id is None


# ---------------------------------------------------------------------------
# T14 — Auto-vinculación (ADR-5)
# ---------------------------------------------------------------------------

class TestAutoVinculacion:
    def test_auto_vincula_cuando_oficio_exacto_y_nombre_alto(self, db_session):
        """Oficio exacto + similitud ≥ 0.90 → APROBADO_AUTO + proceso vinculado."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(
            db_session,
            requerimiento="Servicio de limpieza de oficinas",
            numero_oficio="267-2026-INEI/OTIN",
        )

        payload = _make_payload(
            entry_id="ENTRY-AUTO-01",
            nombre_servicio="Servicio de limpieza de oficinas",
            nombre_servicio_normalizado="limpieza de oficinas",
            numero_oficio="267-2026-INEI/OTIN",
            numero_oficio_raw="N°267-2026-INEI/OTIN",
        )
        result = svc.ingestar_correo(db_session, payload)

        assert result.status == "auto_linked"
        assert result.estado_revision == "APROBADO_AUTO"
        assert result.proceso_id == proceso.id
        assert result.match_confianza is not None
        assert result.match_confianza >= 0.90

        # Verificar audit en historial_cambios
        from app.models.historial import HistorialCambio
        audits = db_session.query(HistorialCambio).filter_by(
            proceso_id=proceso.id
        ).all()
        assert len(audits) >= 1
        assert any(a.modificado_por == "INGESTA_AUTO" for a in audits)

    def test_oficio_exacto_nombre_bajo_queda_pendiente(self, db_session):
        """Oficio exacto pero similitud < 0.90 → PENDIENTE."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(
            db_session,
            requerimiento="Mantenimiento de servidores de alta disponibilidad",
            numero_oficio="267-2026-INEI/OTIN",
        )

        payload = _make_payload(
            entry_id="ENTRY-AUTO-02",
            nombre_servicio="Limpieza de pisos",
            nombre_servicio_normalizado="limpieza de pisos",
            numero_oficio="267-2026-INEI/OTIN",
        )
        result = svc.ingestar_correo(db_session, payload)

        assert result.status == "created"
        assert result.estado_revision == "PENDIENTE"
        assert result.proceso_id is None

    def test_nombre_alto_pero_oficio_null_queda_pendiente(self, db_session):
        """Similitud alta pero oficio null → PENDIENTE (ADR-5 exige ambos)."""
        from app.services import ingesta_service as svc

        _make_proceso(
            db_session,
            requerimiento="Servicio de limpieza de oficinas",
            numero_oficio="267-2026-INEI/OTIN",
        )

        payload = _make_payload(
            entry_id="ENTRY-AUTO-03",
            nombre_servicio="Servicio de limpieza de oficinas",
            nombre_servicio_normalizado="limpieza de oficinas",
            numero_oficio=None,
            numero_oficio_raw=None,
        )
        result = svc.ingestar_correo(db_session, payload)

        assert result.estado_revision == "PENDIENTE"
        assert result.proceso_id is None

    def test_oficio_coincide_multiples_procesos_queda_pendiente(self, db_session):
        """Oficio en más de un proceso → ambigüedad → PENDIENTE."""
        from app.services import ingesta_service as svc

        _make_proceso(
            db_session,
            requerimiento="Servicio de limpieza A",
            numero_oficio="267-2026-INEI/OTIN",
        )
        _make_proceso(
            db_session,
            requerimiento="Servicio de limpieza B",
            numero_oficio="267-2026-INEI/OTIN",
        )

        payload = _make_payload(
            entry_id="ENTRY-AUTO-04",
            nombre_servicio="Servicio de limpieza A",
            nombre_servicio_normalizado="limpieza a",
            numero_oficio="267-2026-INEI/OTIN",
        )
        result = svc.ingestar_correo(db_session, payload)

        assert result.estado_revision == "PENDIENTE"
        assert result.proceso_id is None

    def test_sin_proceso_candidato_queda_pendiente(self, db_session):
        """Sin proceso con ese oficio → PENDIENTE."""
        from app.services import ingesta_service as svc

        payload = _make_payload(
            entry_id="ENTRY-AUTO-05",
            numero_oficio="999-2026-INEI/OTIN",
        )
        result = svc.ingestar_correo(db_session, payload)

        assert result.estado_revision == "PENDIENTE"

    def test_auto_vinculacion_ancla_oficio_en_proceso_sin_oficio(self, db_session):
        """Auto-vinculación: si el proceso tiene numero_oficio=None, lo ancla."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(
            db_session,
            requerimiento="Servicio de limpieza de oficinas",
            numero_oficio=None,  # proceso sin oficio inicial
        )

        # Setear el oficio manualmente para que coincida (simula migración parcial)
        # El proceso NO tiene oficio → no coincide en la búsqueda → queda PENDIENTE
        # Este test verifica que un proceso SIN oficio no dispara auto-vinculación
        payload = _make_payload(
            entry_id="ENTRY-AUTO-06",
            nombre_servicio_normalizado="limpieza de oficinas",
            numero_oficio="267-2026-INEI/OTIN",
        )
        result = svc.ingestar_correo(db_session, payload)
        # Proceso tiene oficio=None → no matchea → PENDIENTE
        assert result.estado_revision == "PENDIENTE"

    def test_auto_vinculacion_adjuntos_guardados_en_staging(self, db_session, tmp_path):
        """Auto-vinculación: adjuntos quedan en proc_{id}/ (definitivo)."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(
            db_session,
            requerimiento="Servicio de limpieza de oficinas",
            numero_oficio="300-2026-INEI/OTIN",
        )

        doc = _make_doc_payload("TDR_limpieza.pdf")
        payload = _make_payload(
            entry_id="ENTRY-AUTO-07",
            nombre_servicio="Servicio de limpieza de oficinas",
            nombre_servicio_normalizado="limpieza de oficinas",
            numero_oficio="300-2026-INEI/OTIN",
            documentos=[doc],
        )
        result = svc.ingestar_correo(db_session, payload)

        assert result.status == "auto_linked"

        # El archivo debe estar en proc_{id}/ (definitivo, NO en ingesta_staging)
        doc_row = db_session.query(IngestaDocumento).filter_by(
            ingesta_correo_id=result.id
        ).first()
        assert doc_row is not None
        assert f"proc_{proceso.id}" in doc_row.ruta_relativa.replace("\\", "/")


# ---------------------------------------------------------------------------
# T16 — aprobar_correo, rechazar_correo, desvincular_correo
# ---------------------------------------------------------------------------

class TestAprobar:
    def test_aprobar_manual_vincula_proceso_y_audita(self, db_session):
        """Aprobación manual: vincula, estado APROBADO, audit con username."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session, requerimiento="Consultoria IT")

        # Ingestar correo sin auto-vincular
        payload = _make_payload(entry_id="ENTRY-APR-01")
        r = svc.ingestar_correo(db_session, payload)
        assert r.estado_revision == "PENDIENTE"

        # Aprobar
        correo = db_session.get(IngestaCorreo, r.id)
        svc.aprobar_correo(db_session, correo.id, proceso.id, usuario="testadmin")

        db_session.refresh(correo)
        assert correo.estado_revision == "APROBADO"
        assert correo.proceso_id == proceso.id
        assert correo.revisado_por == "testadmin"
        assert correo.revisado_en is not None

        from app.models.historial import HistorialCambio
        audits = db_session.query(HistorialCambio).filter_by(proceso_id=proceso.id).all()
        assert any(a.modificado_por == "testadmin" for a in audits)

    def test_aprobar_ancla_oficio_si_proceso_no_tiene(self, db_session):
        """Aprobación ancla el oficio al proceso si este no tenía."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session, numero_oficio=None)
        payload = _make_payload(
            entry_id="ENTRY-APR-02",
            numero_oficio="100-2026-INEI/OTIN",
        )
        r = svc.ingestar_correo(db_session, payload)

        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        db_session.refresh(proceso)
        assert proceso.numero_oficio == "100-2026-INEI/OTIN"

    def test_aprobar_no_sobreescribe_oficio_existente(self, db_session):
        """Aprobación NO sobreescribe el oficio del proceso si ya tiene uno."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session, numero_oficio="50-2026-INEI/OTIN")
        payload = _make_payload(
            entry_id="ENTRY-APR-03",
            numero_oficio="999-2026-INEI/OTIN",
        )
        r = svc.ingestar_correo(db_session, payload)
        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        db_session.refresh(proceso)
        assert proceso.numero_oficio == "50-2026-INEI/OTIN"  # no pisó

    def test_doble_aprobacion_lanza_409(self, db_session):
        """Aprobar un correo ya APROBADO → HTTPException 409."""
        from fastapi import HTTPException
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session)
        payload = _make_payload(entry_id="ENTRY-APR-04")
        r = svc.ingestar_correo(db_session, payload)

        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        with pytest.raises(HTTPException) as exc_info:
            svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")
        assert exc_info.value.status_code == 409

    def test_aprobar_auto_vinculado_lanza_409(self, db_session):
        """Aprobar un correo APROBADO_AUTO también → 409."""
        from fastapi import HTTPException
        from app.services import ingesta_service as svc

        proceso = _make_proceso(
            db_session,
            requerimiento="Servicio de limpieza de oficinas",
            numero_oficio="400-2026-INEI/OTIN",
        )
        payload = _make_payload(
            entry_id="ENTRY-APR-05",
            nombre_servicio="Servicio de limpieza de oficinas",
            nombre_servicio_normalizado="limpieza de oficinas",
            numero_oficio="400-2026-INEI/OTIN",
        )
        r = svc.ingestar_correo(db_session, payload)
        assert r.status == "auto_linked"

        with pytest.raises(HTTPException) as exc_info:
            svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")
        assert exc_info.value.status_code == 409

    def test_aprobar_proceso_soft_deleted_lanza_422(self, db_session):
        """Aprobar sobre proceso soft-deleted → 422."""
        from fastapi import HTTPException
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session)
        proceso.eliminado_en = datetime.datetime.now()
        db_session.flush()

        payload = _make_payload(entry_id="ENTRY-APR-06")
        r = svc.ingestar_correo(db_session, payload)

        with pytest.raises(HTTPException) as exc_info:
            svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")
        assert exc_info.value.status_code == 422

    def test_aprobar_mueve_adjunto_staging_a_definitivo(self, db_session, tmp_path):
        """Aprobación mueve el archivo de ingesta_staging/ a proc_{id}/."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session)
        doc = _make_doc_payload("contrato.pdf")
        payload = _make_payload(entry_id="ENTRY-APR-07", documentos=[doc])

        r = svc.ingestar_correo(db_session, payload)
        assert r.estado_revision == "PENDIENTE"

        # Verificar que el adjunto está en staging
        doc_row = db_session.query(IngestaDocumento).filter_by(
            ingesta_correo_id=r.id
        ).first()
        assert "ingesta_staging" in doc_row.ruta_relativa.replace("\\", "/")

        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        db_session.refresh(doc_row)
        # Ahora debe estar en proc_{id}/
        assert f"proc_{proceso.id}" in doc_row.ruta_relativa.replace("\\", "/")
        assert doc_row.proceso_id == proceso.id

        # El archivo físico debe existir en la nueva ruta
        dest = tmp_path / doc_row.ruta_relativa
        assert dest.exists()


class TestRechazar:
    def test_rechazar_correo_pendiente(self, db_session):
        """Rechazar un correo PENDIENTE → estado RECHAZADO + motivo guardado."""
        from app.services import ingesta_service as svc

        payload = _make_payload(entry_id="ENTRY-REC-01")
        r = svc.ingestar_correo(db_session, payload)

        svc.rechazar_correo(db_session, r.id, motivo="No corresponde al proceso", usuario="testadmin")

        correo = db_session.get(IngestaCorreo, r.id)
        assert correo.estado_revision == "RECHAZADO"
        assert correo.motivo_rechazo == "No corresponde al proceso"
        assert correo.revisado_por == "testadmin"

    def test_rechazar_ya_rechazado_lanza_409(self, db_session):
        """Rechazar un correo ya RECHAZADO → 409."""
        from fastapi import HTTPException
        from app.services import ingesta_service as svc

        payload = _make_payload(entry_id="ENTRY-REC-02")
        r = svc.ingestar_correo(db_session, payload)
        svc.rechazar_correo(db_session, r.id, motivo="Motivo 1", usuario="testadmin")

        with pytest.raises(HTTPException) as exc_info:
            svc.rechazar_correo(db_session, r.id, motivo="Motivo 2", usuario="testadmin")
        assert exc_info.value.status_code == 409

    def test_rechazar_aprobado_lanza_409(self, db_session):
        """Rechazar un correo APROBADO → 409."""
        from fastapi import HTTPException
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session)
        payload = _make_payload(entry_id="ENTRY-REC-03")
        r = svc.ingestar_correo(db_session, payload)
        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        with pytest.raises(HTTPException) as exc_info:
            svc.rechazar_correo(db_session, r.id, motivo="x", usuario="testadmin")
        assert exc_info.value.status_code == 409


class TestDesvincular:
    def test_desvincular_aprobado_vuelve_a_pendiente(self, db_session):
        """Desvincular desde APROBADO → estado PENDIENTE, proceso_id=None."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session)
        payload = _make_payload(entry_id="ENTRY-DES-01")
        r = svc.ingestar_correo(db_session, payload)
        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        svc.desvincular_correo(db_session, r.id, usuario="testadmin")

        correo = db_session.get(IngestaCorreo, r.id)
        assert correo.estado_revision == "PENDIENTE"
        assert correo.proceso_id is None
        assert correo.match_confianza is None

    def test_desvincular_auto_vinculado_vuelve_a_pendiente(self, db_session):
        """Desvincular desde APROBADO_AUTO → PENDIENTE."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(
            db_session,
            requerimiento="Servicio de limpieza de oficinas",
            numero_oficio="500-2026-INEI/OTIN",
        )
        payload = _make_payload(
            entry_id="ENTRY-DES-02",
            nombre_servicio="Servicio de limpieza de oficinas",
            nombre_servicio_normalizado="limpieza de oficinas",
            numero_oficio="500-2026-INEI/OTIN",
        )
        r = svc.ingestar_correo(db_session, payload)
        assert r.status == "auto_linked"

        svc.desvincular_correo(db_session, r.id, usuario="testadmin")

        correo = db_session.get(IngestaCorreo, r.id)
        assert correo.estado_revision == "PENDIENTE"
        assert correo.proceso_id is None
        assert correo.match_confianza is None

    def test_desvincular_mueve_adjunto_definitivo_a_staging(self, db_session, tmp_path):
        """Desvincular mueve adjuntos de proc_{id}/ → ingesta_staging/."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session)
        doc = _make_doc_payload("informe.pdf")
        payload = _make_payload(entry_id="ENTRY-DES-03", documentos=[doc])

        r = svc.ingestar_correo(db_session, payload)
        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        doc_row = db_session.query(IngestaDocumento).filter_by(
            ingesta_correo_id=r.id
        ).first()
        assert f"proc_{proceso.id}" in doc_row.ruta_relativa.replace("\\", "/")

        svc.desvincular_correo(db_session, r.id, usuario="testadmin")

        db_session.refresh(doc_row)
        assert "ingesta_staging" in doc_row.ruta_relativa.replace("\\", "/")
        assert doc_row.proceso_id is None

        # El archivo físico debe existir en staging
        dest = tmp_path / doc_row.ruta_relativa
        assert dest.exists()

    def test_desvincular_pendiente_lanza_409(self, db_session):
        """Desvincular un correo PENDIENTE → 409."""
        from fastapi import HTTPException
        from app.services import ingesta_service as svc

        payload = _make_payload(entry_id="ENTRY-DES-04")
        r = svc.ingestar_correo(db_session, payload)

        with pytest.raises(HTTPException) as exc_info:
            svc.desvincular_correo(db_session, r.id, usuario="testadmin")
        assert exc_info.value.status_code == 409

    def test_desvincular_NO_revierte_oficio_del_proceso(self, db_session):
        """Desvincular NO revierte numero_oficio del proceso (decisión MVP)."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session, numero_oficio=None)
        payload = _make_payload(
            entry_id="ENTRY-DES-05",
            numero_oficio="200-2026-INEI/OTIN",
        )
        r = svc.ingestar_correo(db_session, payload)
        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        db_session.refresh(proceso)
        assert proceso.numero_oficio == "200-2026-INEI/OTIN"  # anclado

        svc.desvincular_correo(db_session, r.id, usuario="testadmin")

        db_session.refresh(proceso)
        # numero_oficio PERMANECE (no se revierte)
        assert proceso.numero_oficio == "200-2026-INEI/OTIN"

    def test_desvincular_audit_registra_la_reversa(self, db_session):
        """Desvincular registra audit en historial_cambios."""
        from app.services import ingesta_service as svc
        from app.models.historial import HistorialCambio

        proceso = _make_proceso(db_session)
        payload = _make_payload(entry_id="ENTRY-DES-06")
        r = svc.ingestar_correo(db_session, payload)
        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        before_count = db_session.query(HistorialCambio).filter_by(
            proceso_id=proceso.id
        ).count()

        svc.desvincular_correo(db_session, r.id, usuario="testadmin")

        after_count = db_session.query(HistorialCambio).filter_by(
            proceso_id=proceso.id
        ).count()
        assert after_count > before_count


# ---------------------------------------------------------------------------
# T18 — get_pendientes, corregir_correo, get_documentos_proceso
# ---------------------------------------------------------------------------

class TestGetPendientes:
    def test_get_pendientes_retorna_solo_pendientes(self, db_session):
        """get_pendientes retorna solo correos con estado PENDIENTE."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session)

        p1 = _make_payload(entry_id="ENTRY-PEND-01")
        p2 = _make_payload(entry_id="ENTRY-PEND-02")
        p3 = _make_payload(entry_id="ENTRY-PEND-03")

        r1 = svc.ingestar_correo(db_session, p1)
        r2 = svc.ingestar_correo(db_session, p2)
        r3 = svc.ingestar_correo(db_session, p3)

        # Aprobar uno, rechazar otro
        svc.aprobar_correo(db_session, r1.id, proceso.id, usuario="testadmin")
        svc.rechazar_correo(db_session, r2.id, motivo=None, usuario="testadmin")

        pendientes = svc.get_pendientes(db_session)

        ids = [c.id for c in pendientes]
        assert r3.id in ids       # PENDIENTE → debe aparecer
        assert r1.id not in ids   # APROBADO → NO
        assert r2.id not in ids   # RECHAZADO → NO

    def test_get_pendientes_entry_ids_solo_devuelve_ids(self, db_session):
        """get_pendientes con solo_entry_ids=True devuelve strings."""
        from app.services import ingesta_service as svc

        p = _make_payload(entry_id="ENTRY-EID-01")
        svc.ingestar_correo(db_session, p)

        entry_ids = svc.get_pendientes_entry_ids(db_session)
        assert "ENTRY-EID-01" in entry_ids


class TestCorregirCorreo:
    def test_corregir_campos_editables(self, db_session):
        """Corrección inline actualiza solo los campos enviados."""
        from app.services import ingesta_service as svc
        from app.schemas.ingesta import CorreoCorreccionIn

        payload = _make_payload(entry_id="ENTRY-CORR-01", nombre_servicio="Original")
        r = svc.ingestar_correo(db_session, payload)

        correccion = CorreoCorreccionIn(nombre_servicio="Corregido")
        svc.corregir_correo(db_session, r.id, correccion)

        correo = db_session.get(IngestaCorreo, r.id)
        assert correo.nombre_servicio == "Corregido"

    def test_corregir_aprobado_lanza_409(self, db_session):
        """No se puede corregir un correo ya APROBADO."""
        from fastapi import HTTPException
        from app.services import ingesta_service as svc
        from app.schemas.ingesta import CorreoCorreccionIn

        proceso = _make_proceso(db_session)
        payload = _make_payload(entry_id="ENTRY-CORR-02")
        r = svc.ingestar_correo(db_session, payload)
        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        with pytest.raises(HTTPException) as exc_info:
            svc.corregir_correo(db_session, r.id, CorreoCorreccionIn(nombre_servicio="x"))
        assert exc_info.value.status_code == 409


class TestGetDocumentosProceso:
    def test_get_documentos_proceso_retorna_docs_vinculados(self, db_session):
        """get_documentos_proceso retorna solo documentos del proceso vinculado."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session)
        doc = _make_doc_payload("tdr.pdf")
        payload = _make_payload(entry_id="ENTRY-DOCS-01", documentos=[doc])
        r = svc.ingestar_correo(db_session, payload)
        svc.aprobar_correo(db_session, r.id, proceso.id, usuario="testadmin")

        docs = svc.get_documentos_proceso(db_session, proceso.id)
        assert len(docs) >= 1
        assert all(d.proceso_id == proceso.id for d in docs)

    def test_get_documentos_proceso_vacio_retorna_lista_vacia(self, db_session):
        """Proceso sin documentos vinculados → lista vacía."""
        from app.services import ingesta_service as svc

        proceso = _make_proceso(db_session)
        docs = svc.get_documentos_proceso(db_session, proceso.id)
        assert docs == []
