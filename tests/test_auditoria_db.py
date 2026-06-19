"""Tests de auditoría de BD — constraints únicos y validaciones de estado.

Cubre:
(a) POST duplicado de etapa simple en proceso activo → 409
(b) POST en proceso CULMINADO → 409
(c) sync_montos con upsert funciona idempotente (doble llamada, mismo resultado)
(d) Los 3 índices únicos existen en la BD
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models.etapa import EtapaRegistro
from app.models.montos import MontosProceso
from app.models.proceso import Proceso
from app.services.etapas_catalogo import ETAPAS_CATALOGO
from app.services.etapas_service import sync_montos


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proceso(db_session, estado: str = "EN PROCESO") -> Proceso:
    p = Proceso(
        id_proceso=f"2026-AUD-TST-{estado[:3]}",
        requerimiento=f"Test auditoría {estado}",
        tipo="SERVICIO",
        areas_usuarias=["DTDIS"],
        estado=estado,
        anno=2026,
        creado_por="testuser",
    )
    db_session.add(p)
    db_session.flush()
    return p


def _insert_etapa(db_session, proceso_id: int, cod: str, **kwargs) -> EtapaRegistro:
    spec = ETAPAS_CATALOGO.get(cod)
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=spec.nombre if spec else cod,
        area_responsable=spec.area_responsable if spec else "OTIN",
        estado_etapa=kwargs.pop("estado_etapa", "COMPLETADO"),
        nro_ronda=kwargs.pop("nro_ronda", 1),
        es_bucle=spec.es_bucle if spec else False,
        registrado_por="testsetup",
        **kwargs,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _post_etapa(client, headers, proceso_id: int, cod: str, **extra):
    payload = {
        "codigo_etapa": cod,
        "nombre_etapa": f"Etapa {cod}",
        "fecha_inicio": "2026-06-01",
        "estado_etapa": "PENDIENTE",
    }
    payload.update(extra)
    return client.post(f"/procesos/{proceso_id}/etapas", json=payload, headers=headers)


# ---------------------------------------------------------------------------
# (a) Duplicado de etapa simple → 409
# ---------------------------------------------------------------------------

class TestDuplicadoEtapaSimple:
    """POST duplicado de etapa simple en proceso activo debe retornar 409."""

    def test_post_duplicado_simple_devuelve_409(self, client, editor_headers, db_session):
        # Crear proceso activo con E02 ya registrada
        proceso = _make_proceso(db_session, "EN PROCESO")
        # E01 es por-área, usamos E02 que es simple
        _insert_etapa(db_session, proceso.id, "E02")
        db_session.commit()

        # Intentar registrar E02 de nuevo
        resp = _post_etapa(client, editor_headers, proceso.id, "E02")
        assert resp.status_code == 409, f"Esperaba 409, got {resp.status_code}: {resp.text}"

    def test_post_primer_registro_simple_ok(self, client, editor_headers, db_session):
        """El primer registro de una etapa simple siempre debe funcionar.

        flujo-real-otin-v2: E03 prereq is E02b (not E02 directly).
        Insert E02 and E02b COMPLETADO so E03 can be registered.
        """
        proceso = _make_proceso(db_session, "EN PROCESO")
        # flujo-real-otin-v2: E03 prereq chain is E02 → E02b
        _insert_etapa(db_session, proceso.id, "E02", estado_etapa="COMPLETADO")
        _insert_etapa(db_session, proceso.id, "E02b", estado_etapa="COMPLETADO")
        db_session.commit()

        resp = _post_etapa(client, editor_headers, proceso.id, "E03")
        assert resp.status_code == 201, f"Esperaba 201, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# (b) POST en proceso CULMINADO → 409
# ---------------------------------------------------------------------------

class TestProcesosCulminado:
    """Un proceso CULMINADO no debe aceptar nuevas etapas."""

    def test_post_etapa_en_proceso_culminado_devuelve_409(
        self, client, editor_headers, db_session
    ):
        proceso = _make_proceso(db_session, "CULMINADO")
        db_session.commit()

        resp = _post_etapa(client, editor_headers, proceso.id, "E02")
        assert resp.status_code == 409, f"Esperaba 409, got {resp.status_code}: {resp.text}"
        assert "culminado" in resp.json()["detail"].lower()

    def test_post_etapa_en_proceso_activo_no_bloqueado(
        self, client, editor_headers, db_session
    ):
        """Control: proceso EN PROCESO no bloquea por estado."""
        proceso = _make_proceso(db_session, "EN PROCESO")
        _insert_etapa(db_session, proceso.id, "E02", estado_etapa="COMPLETADO")
        db_session.commit()

        resp = _post_etapa(client, editor_headers, proceso.id, "E03")
        # Puede fallar por prereq u otra regla, pero NOT por CULMINADO (no 409 con ese msg)
        if resp.status_code == 409:
            assert "culminado" not in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# (c) sync_montos idempotente con upsert nativo
# ---------------------------------------------------------------------------

class TestSyncMontosUpsert:
    """sync_montos debe ser idempotente — doble llamada produce el mismo resultado."""

    def test_sync_montos_e09_idempotente(self, db_session):
        proceso = _make_proceso(db_session, "EN PROCESO")
        etapa = _insert_etapa(
            db_session,
            proceso.id,
            "E09",
            estado_etapa="COMPLETADO",
            monto_cert=Decimal("15000.00"),
        )
        db_session.commit()

        # Primera llamada — debe crear la fila
        sync_montos(db_session, proceso.id, "E09", etapa)
        db_session.flush()

        montos1 = db_session.execute(
            select(MontosProceso).where(MontosProceso.proceso_id == proceso.id)
        ).scalars().first()
        assert montos1 is not None
        assert montos1.valor_em == Decimal("15000.00")

        # Segunda llamada — debe actualizar sin error (upsert idempotente)
        sync_montos(db_session, proceso.id, "E09", etapa)
        db_session.flush()

        montos2 = db_session.execute(
            select(MontosProceso).where(MontosProceso.proceso_id == proceso.id)
        ).scalars().first()
        assert montos2.valor_em == Decimal("15000.00")

        # Solo debe haber UNA fila (no duplicados)
        count = db_session.execute(
            select(MontosProceso).where(MontosProceso.proceso_id == proceso.id)
        ).scalars().all()
        assert len(count) == 1

    def test_sync_montos_e19_idempotente(self, db_session):
        proceso = _make_proceso(db_session, "EN PROCESO")
        etapa = _insert_etapa(
            db_session,
            proceso.id,
            "E19",
            estado_etapa="COMPLETADO",
            nro_ocs="OCS-2026-001",
            monto_ocs=Decimal("50000.00"),
            plazo_entrega=30,
        )
        db_session.commit()

        sync_montos(db_session, proceso.id, "E19", etapa)
        db_session.flush()
        sync_montos(db_session, proceso.id, "E19", etapa)
        db_session.flush()

        rows = db_session.execute(
            select(MontosProceso).where(MontosProceso.proceso_id == proceso.id)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].nro_ocs == "OCS-2026-001"
        assert rows[0].plazo_entrega == 30


# ---------------------------------------------------------------------------
# (d) Índices únicos en BD
# ---------------------------------------------------------------------------

class TestIndicesUnicosExisten:
    """Verifica que los 3 índices únicos parciales fueron creados en PostgreSQL."""

    def test_indice_simple_existe(self, db_session):
        result = db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'etapas_registro' "
                "AND indexname = 'uq_etapa_simple_por_proceso'"
            )
        ).fetchone()
        assert result is not None, "Índice uq_etapa_simple_por_proceso no encontrado"

    def test_indice_por_area_existe(self, db_session):
        result = db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'etapas_registro' "
                "AND indexname = 'uq_etapa_por_area'"
            )
        ).fetchone()
        assert result is not None, "Índice uq_etapa_por_area no encontrado"

    def test_indice_ronda_bucle_existe(self, db_session):
        result = db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'etapas_registro' "
                "AND indexname = 'uq_ronda_bucle'"
            )
        ).fetchone()
        assert result is not None, "Índice uq_ronda_bucle no encontrado"
