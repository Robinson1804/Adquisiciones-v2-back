"""Unit tests for schemas/ingesta.py — T06 (RED → GREEN).

Pure Pydantic v2 validation tests; no DB required.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.ingesta import (
    AprobarIn,
    CorreoCorreccionIn,
    CorreoIngestaIn,
    CorreoIngestaOut,
    DocumentoExtraidoIn,
    IngestaCorreoResultOut,
    RechazarIn,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _documento_minimo(**overrides) -> dict:
    base = {
        "nombre_original": "TDR_v2.pdf",
        "content_type": "application/pdf",
        "tamano_bytes": 102400,
        "contenido_b64": "dGVzdA==",
        "tipo_clasificado": "TDR",
        "confianza": 0.92,
    }
    base.update(overrides)
    return base


def _correo_minimo(**overrides) -> dict:
    base = {
        "entry_id": "ENTRY001",
        "subject": "Oficio N°267-2026-INEI/OTIN: TDR Limpieza",
        "sender_name": "Juan Pérez",
        "sender_email": "jperez@example.com",
        "received_at": "2026-06-10T09:00:00",
        "body_clean": "Se adjunta TDR para evaluación.",
        "nombre_servicio": "Servicio de limpieza de oficinas",
        "nombre_servicio_normalizado": "limpieza oficinas",
        "documentos": [_documento_minimo()],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# DocumentoExtraidoIn
# ---------------------------------------------------------------------------

class TestDocumentoExtraidoIn:
    def test_valid_documento(self):
        doc = DocumentoExtraidoIn(**_documento_minimo())
        assert doc.nombre_original == "TDR_v2.pdf"
        assert doc.confianza == 0.92

    def test_content_type_invalido_rechazado(self):
        """content_type fuera de la whitelist debe ser rechazado."""
        with pytest.raises(ValidationError) as exc_info:
            DocumentoExtraidoIn(**_documento_minimo(content_type="application/x-executable"))
        assert "content_type" in str(exc_info.value).lower() or exc_info.value is not None

    def test_confianza_fuera_de_rango_alto(self):
        """confianza > 1.0 debe ser rechazado."""
        with pytest.raises(ValidationError):
            DocumentoExtraidoIn(**_documento_minimo(confianza=1.5))

    def test_confianza_fuera_de_rango_bajo(self):
        """confianza < 0.0 debe ser rechazado."""
        with pytest.raises(ValidationError):
            DocumentoExtraidoIn(**_documento_minimo(confianza=-0.1))

    def test_tipo_clasificado_valido(self):
        for tipo in ["TDR", "COTIZACION", "ORDEN_SERVICIO", "CONFORMIDAD",
                     "INFORME", "OFICIO", "CRONOGRAMA", "SIAF", "OTRO"]:
            doc = DocumentoExtraidoIn(**_documento_minimo(tipo_clasificado=tipo))
            assert doc.tipo_clasificado == tipo

    def test_tipo_clasificado_invalido(self):
        with pytest.raises(ValidationError):
            DocumentoExtraidoIn(**_documento_minimo(tipo_clasificado="DESCONOCIDO"))

    def test_nombre_original_requerido(self):
        data = _documento_minimo()
        del data["nombre_original"]
        with pytest.raises(ValidationError):
            DocumentoExtraidoIn(**data)

    def test_tamano_bytes_positivo(self):
        with pytest.raises(ValidationError):
            DocumentoExtraidoIn(**_documento_minimo(tamano_bytes=-1))


# ---------------------------------------------------------------------------
# CorreoIngestaIn
# ---------------------------------------------------------------------------

class TestCorreoIngestaIn:
    def test_correo_valido_completo(self):
        correo = CorreoIngestaIn(**_correo_minimo(
            numero_oficio_raw="OFICIO N° 267-2026-INEI/OTIN",
            numero_oficio="267-2026-INEI/OTIN",
            oss=["OS-001", "OS-002"],
            tipo="SERVICIO",
        ))
        assert correo.entry_id == "ENTRY001"
        assert correo.numero_oficio == "267-2026-INEI/OTIN"

    def test_numero_oficio_raw_none_valido(self):
        """numero_oficio_raw=None es válido (correo sin oficio)."""
        correo = CorreoIngestaIn(**_correo_minimo(numero_oficio_raw=None, numero_oficio=None))
        assert correo.numero_oficio_raw is None
        assert correo.numero_oficio is None

    def test_oss_vacio_valido(self):
        correo = CorreoIngestaIn(**_correo_minimo(oss=[]))
        assert correo.oss == []

    def test_documentos_vacio_valido(self):
        """Lista de documentos vacía es aceptable."""
        correo = CorreoIngestaIn(**_correo_minimo(documentos=[]))
        assert correo.documentos == []

    def test_tipo_bien_o_servicio(self):
        for tipo in ["BIEN", "SERVICIO"]:
            correo = CorreoIngestaIn(**_correo_minimo(tipo=tipo))
            assert correo.tipo == tipo

    def test_tipo_invalido_rechazado(self):
        with pytest.raises(ValidationError):
            CorreoIngestaIn(**_correo_minimo(tipo="OTRO"))

    def test_entry_id_requerido(self):
        data = _correo_minimo()
        del data["entry_id"]
        with pytest.raises(ValidationError):
            CorreoIngestaIn(**data)

    def test_received_at_acepta_string_iso(self):
        correo = CorreoIngestaIn(**_correo_minimo(received_at="2026-06-15T10:30:00"))
        assert correo.received_at is not None


# ---------------------------------------------------------------------------
# AprobarIn
# ---------------------------------------------------------------------------

class TestAprobarIn:
    def test_proceso_id_requerido(self):
        """AprobarIn sin proceso_id debe fallar."""
        with pytest.raises(ValidationError):
            AprobarIn()

    def test_proceso_id_int(self):
        a = AprobarIn(proceso_id=42)
        assert a.proceso_id == 42

    def test_proceso_id_debe_ser_positivo(self):
        """proceso_id <= 0 no tiene sentido (PK de proceso)."""
        with pytest.raises(ValidationError):
            AprobarIn(proceso_id=0)

    def test_proceso_id_negativo_rechazado(self):
        with pytest.raises(ValidationError):
            AprobarIn(proceso_id=-1)


# ---------------------------------------------------------------------------
# IngestaCorreoResultOut
# ---------------------------------------------------------------------------

class TestIngestaCorreoResultOut:
    def test_status_created(self):
        r = IngestaCorreoResultOut(id=1, estado_revision="PENDIENTE", status="created")
        assert r.status == "created"

    def test_status_auto_linked(self):
        r = IngestaCorreoResultOut(
            id=2,
            estado_revision="APROBADO_AUTO",
            proceso_id=5,
            match_confianza=0.95,
            status="auto_linked",
        )
        assert r.status == "auto_linked"
        assert r.match_confianza == 0.95

    def test_status_already_ingested(self):
        r = IngestaCorreoResultOut(id=3, estado_revision="PENDIENTE", status="already_ingested")
        assert r.status == "already_ingested"

    def test_status_invalido(self):
        with pytest.raises(ValidationError):
            IngestaCorreoResultOut(id=1, estado_revision="PENDIENTE", status="unknown_status")


# ---------------------------------------------------------------------------
# RechazarIn
# ---------------------------------------------------------------------------

class TestRechazarIn:
    def test_motivo_opcional(self):
        """motivo es opcional en RechazarIn."""
        r = RechazarIn()
        assert r.motivo is None

    def test_motivo_con_texto(self):
        r = RechazarIn(motivo="No corresponde al proceso indicado.")
        assert r.motivo == "No corresponde al proceso indicado."


# ---------------------------------------------------------------------------
# CorreoCorreccionIn
# ---------------------------------------------------------------------------

class TestCorreoCorreccionIn:
    def test_todos_opcionales(self):
        """CorreoCorreccionIn vacío (PATCH parcial) debe ser válido."""
        c = CorreoCorreccionIn()
        assert c.nombre_servicio is None

    def test_patch_parcial_nombre(self):
        c = CorreoCorreccionIn(nombre_servicio="Limpieza corregida")
        assert c.nombre_servicio == "Limpieza corregida"

    def test_patch_parcial_oficio(self):
        c = CorreoCorreccionIn(numero_oficio="267-2026-INEI/OTIN")
        assert c.numero_oficio == "267-2026-INEI/OTIN"
