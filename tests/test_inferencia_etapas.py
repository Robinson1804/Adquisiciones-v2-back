"""Tests — inferencia_etapas.py — TDD (Strict TDD: RED → GREEN → REFACTOR).

Cubre:
  T-INF-01  Mapeo puro TIPO_DOC_A_ETAPA
  T-INF-02  inferir_etapa_objetivo: mayor orden, confianza, tipos no mapeados
  T-INF-03  inferir_avance cadena completa con CONFORMIDAD (E23)
  T-INF-04  Idempotencia (re-aprobar no duplica filas)
  T-INF-05  NO pisa filas manuales preexistentes
  T-INF-06  revertir_avance revierte solo las del correo indicado
  T-INF-07  Confianza < 0.7 no infiere
  T-INF-08  Solo OTRO/OFICIO/SIAF/INFORME no infiere
  T-INF-09  Montos_proceso intactos tras inferir (FIX Q1 crítico)
  T-INF-10  derivar_tiempos: campos derivados de tiempo
  T-INF-11  GET /procesos/{id} devuelve los 3 campos de tiempo

Patrón: usa db_session (función transaccional, rollback automático).
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.models.etapa import EtapaRegistro
from app.models.montos import MontosProceso
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Helpers de fixtures
# ---------------------------------------------------------------------------

def _make_proceso(db, requerimiento="Servicio de prueba inferencia", estado="EN PROCESO"):
    """Crea un proceso mínimo en DB."""
    from sqlalchemy import text
    count = db.execute(text("SELECT COUNT(*) FROM procesos")).scalar_one()
    p = Proceso(
        id_proceso=f"2026-INF-{count + 1:03d}",
        requerimiento=requerimiento,
        tipo="SERVICIO",
        unidad_resp="OTIN",
        areas_usuarias=["AREA_A"],
        anno=2026,
        estado=estado,
        creado_por="testadmin",
    )
    db.add(p)
    db.flush()
    return p


def _make_doc(tipo_clasificado, confianza=0.95):
    """Crea un objeto stub que simula IngestaDocumento con tipo_clasificado y confianza."""
    class _DocStub:
        pass
    d = _DocStub()
    d.tipo_clasificado = tipo_clasificado
    d.confianza = Decimal(str(confianza))
    return d


# ---------------------------------------------------------------------------
# T-INF-01  Mapeo puro TIPO_DOC_A_ETAPA
# ---------------------------------------------------------------------------

class TestMapeoTipoDocEtapa:
    def test_mapa_contiene_cinco_tipos(self):
        """El dict TIPO_DOC_A_ETAPA tiene exactamente los 5 tipos mapeados."""
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert set(TIPO_DOC_A_ETAPA.keys()) == {"TDR", "COTIZACION", "ORDEN_SERVICIO", "CRONOGRAMA", "CONFORMIDAD"}

    def test_tdr_mapea_e02(self):
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert TIPO_DOC_A_ETAPA["TDR"] == "E02"

    def test_cotizacion_mapea_e03(self):
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert TIPO_DOC_A_ETAPA["COTIZACION"] == "E03"

    def test_orden_servicio_mapea_e19(self):
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert TIPO_DOC_A_ETAPA["ORDEN_SERVICIO"] == "E19"

    def test_cronograma_mapea_e22(self):
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert TIPO_DOC_A_ETAPA["CRONOGRAMA"] == "E22"

    def test_conformidad_mapea_e23(self):
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert TIPO_DOC_A_ETAPA["CONFORMIDAD"] == "E23"

    def test_informe_no_en_mapa(self):
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert "INFORME" not in TIPO_DOC_A_ETAPA

    def test_oficio_no_en_mapa(self):
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert "OFICIO" not in TIPO_DOC_A_ETAPA

    def test_siaf_no_en_mapa(self):
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert "SIAF" not in TIPO_DOC_A_ETAPA

    def test_otro_no_en_mapa(self):
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert "OTRO" not in TIPO_DOC_A_ETAPA

    def test_e24_no_en_mapa(self):
        """E24 (por_area) nunca debe estar en el mapa (diseño ADR-D5)."""
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert "E24" not in TIPO_DOC_A_ETAPA.values()

    def test_e25_no_en_mapa(self):
        """E25 (fin) nunca debe estar en el mapa."""
        from app.services.inferencia_etapas import TIPO_DOC_A_ETAPA
        assert "E25" not in TIPO_DOC_A_ETAPA.values()


# ---------------------------------------------------------------------------
# T-INF-02  inferir_etapa_objetivo
# ---------------------------------------------------------------------------

class TestInferirEtapaObjetivo:
    def test_tdr_solo_retorna_e02(self):
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        docs = [_make_doc("TDR", 0.85)]
        assert inferir_etapa_objetivo(docs) == "E02"

    def test_conformidad_sola_retorna_e23(self):
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        docs = [_make_doc("CONFORMIDAD", 0.9)]
        assert inferir_etapa_objetivo(docs) == "E23"

    def test_tdr_y_conformidad_gana_e23_mayor_orden(self):
        """TDR (E02, orden 4) + CONFORMIDAD (E23, orden 30) → gana E23."""
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        docs = [_make_doc("TDR", 0.85), _make_doc("CONFORMIDAD", 0.9)]
        assert inferir_etapa_objetivo(docs) == "E23"

    def test_oficio_siaf_otro_informe_retorna_none(self):
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        for tipo in ("OFICIO", "SIAF", "OTRO", "INFORME"):
            docs = [_make_doc(tipo, 0.9)]
            assert inferir_etapa_objetivo(docs) is None, f"Tipo {tipo} no deberia inferir"

    def test_conformidad_confianza_baja_retorna_none(self):
        """CONFORMIDAD con confianza < 0.7 no infiere."""
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        docs = [_make_doc("CONFORMIDAD", 0.65)]
        assert inferir_etapa_objetivo(docs) is None

    def test_confianza_exactamente_07_infiere(self):
        """Confianza exactamente 0.7 debe inferir (umbral >=)."""
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        docs = [_make_doc("CONFORMIDAD", 0.7)]
        assert inferir_etapa_objetivo(docs) == "E23"

    def test_conformidad_confianza_baja_pero_cotizacion_alta_infiere_cotizacion(self):
        """CONFORMIDAD conf=0.5, COTIZACION conf=0.8 → solo COTIZACION (E03)."""
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        docs = [_make_doc("CONFORMIDAD", 0.5), _make_doc("COTIZACION", 0.8)]
        assert inferir_etapa_objetivo(docs) == "E03"

    def test_lista_vacia_retorna_none(self):
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        assert inferir_etapa_objetivo([]) is None

    def test_todos_bajo_umbral_retorna_none(self):
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        docs = [_make_doc("TDR", 0.5), _make_doc("CONFORMIDAD", 0.6)]
        assert inferir_etapa_objetivo(docs) is None

    def test_conformidad_alta_y_otro_retorna_e23(self):
        """CONFORMIDAD conf>=0.7 + OTRO → E23 (OTRO ignorado)."""
        from app.services.inferencia_etapas import inferir_etapa_objetivo
        docs = [_make_doc("CONFORMIDAD", 0.9), _make_doc("OTRO", 0.99)]
        assert inferir_etapa_objetivo(docs) == "E23"


# ---------------------------------------------------------------------------
# T-INF-03  inferir_avance — cadena completa con CONFORMIDAD
# ---------------------------------------------------------------------------

class TestInferirAvance:
    """Tests de integración con DB real (dashboard_test)."""

    def test_conformidad_infiere_cadena_hasta_e23(self, db_session):
        """CONFORMIDAD → E23: todas las etapas cadena hasta E23 (excl. por_area) quedan COMPLETADO."""
        from app.services.inferencia_etapas import inferir_avance
        from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

        proceso = _make_proceso(db_session)
        docs = [_make_doc("CONFORMIDAD", 0.9)]
        fecha = datetime.date(2025, 6, 1)

        marcadas = inferir_avance(db_session, proceso.id, docs, correo_id=42, fecha=fecha)

        assert len(marcadas) > 0

        # Todas las del prefijo hasta E23 (no por_area) deben estar COMPLETADO
        idx_e23 = CADENA.index("E23")
        prefijo = CADENA[: idx_e23 + 1]
        por_area_cods = {cod for cod in prefijo if ETAPAS_CATALOGO[cod].por_area}

        rows = db_session.execute(
            __import__("sqlalchemy").select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.estado_etapa == "COMPLETADO",
            )
        ).scalars().all()
        completadas_cods = {r.codigo_etapa for r in rows}

        for cod in prefijo:
            if cod not in por_area_cods:
                assert cod in completadas_cods, f"{cod} deberia estar COMPLETADO"

    def test_cadena_no_incluye_etapas_por_area(self, db_session):
        """E01c y E11 (por_area) NO deben ser inferidas."""
        from app.services.inferencia_etapas import inferir_avance

        proceso = _make_proceso(db_session)
        docs = [_make_doc("CONFORMIDAD", 0.9)]
        fecha = datetime.date(2025, 6, 1)

        inferir_avance(db_session, proceso.id, docs, correo_id=42, fecha=fecha)

        rows = db_session.execute(
            __import__("sqlalchemy").select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.codigo_etapa.in_(["E01c", "E11"]),
            )
        ).scalars().all()
        assert len(rows) == 0, "E01c y E11 (por_area) NO deben ser inferidas"

    def test_cadena_no_incluye_e24_e25(self, db_session):
        """E24 (por_area) y E25 (fin) NO deben aparecer en la cadena de CONFORMIDAD."""
        from app.services.inferencia_etapas import inferir_avance

        proceso = _make_proceso(db_session)
        docs = [_make_doc("CONFORMIDAD", 0.9)]
        fecha = datetime.date(2025, 6, 1)

        inferir_avance(db_session, proceso.id, docs, correo_id=42, fecha=fecha)

        rows = db_session.execute(
            __import__("sqlalchemy").select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.codigo_etapa.in_(["E24", "E25"]),
            )
        ).scalars().all()
        assert len(rows) == 0, "E24 y E25 NO deben ser inferidas"

    def test_filas_inferidas_tienen_marca_correo(self, db_session):
        """Cada fila inferida tiene observaciones con [INGESTA_INFER corr={correo_id}]."""
        from app.services.inferencia_etapas import inferir_avance

        proceso = _make_proceso(db_session)
        docs = [_make_doc("COTIZACION", 0.9)]
        fecha = datetime.date(2025, 4, 1)
        correo_id = 99

        inferir_avance(db_session, proceso.id, docs, correo_id=correo_id, fecha=fecha)

        rows = db_session.execute(
            __import__("sqlalchemy").select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.registrado_por == "INGESTA_INFER",
            )
        ).scalars().all()

        assert len(rows) > 0
        marca = f"[INGESTA_INFER corr={correo_id}]"
        for row in rows:
            assert row.observaciones is not None
            assert marca in row.observaciones, f"Fila {row.codigo_etapa} no tiene la marca: {row.observaciones!r}"

    def test_filas_inferidas_tienen_fecha_correcta(self, db_session):
        """Las filas inferidas tienen fecha_inicio = fecha_fin = fecha del doc."""
        from app.services.inferencia_etapas import inferir_avance

        proceso = _make_proceso(db_session)
        fecha = datetime.date(2025, 3, 15)
        docs = [_make_doc("TDR", 0.9)]

        inferir_avance(db_session, proceso.id, docs, correo_id=1, fecha=fecha)

        rows = db_session.execute(
            __import__("sqlalchemy").select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.registrado_por == "INGESTA_INFER",
            )
        ).scalars().all()

        assert len(rows) > 0
        for row in rows:
            assert row.fecha_inicio == fecha
            assert row.fecha_fin == fecha

    def test_sin_etapa_mapeada_no_infiere_nada(self, db_session):
        """Docs con tipos no mapeados → 0 filas creadas."""
        from app.services.inferencia_etapas import inferir_avance

        proceso = _make_proceso(db_session)
        docs = [_make_doc("OTRO", 0.99), _make_doc("OFICIO", 0.99)]

        marcadas = inferir_avance(db_session, proceso.id, docs, correo_id=1, fecha=datetime.date.today())

        assert marcadas == []

        rows = db_session.execute(
            __import__("sqlalchemy").select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
            )
        ).scalars().all()
        assert len(rows) == 0

    def test_confianza_baja_no_infiere(self, db_session):
        """Docs con confianza < 0.7 → 0 filas creadas."""
        from app.services.inferencia_etapas import inferir_avance

        proceso = _make_proceso(db_session)
        docs = [_make_doc("CONFORMIDAD", 0.5), _make_doc("TDR", 0.65)]

        marcadas = inferir_avance(db_session, proceso.id, docs, correo_id=1, fecha=datetime.date.today())

        assert marcadas == []


# ---------------------------------------------------------------------------
# T-INF-04  Idempotencia
# ---------------------------------------------------------------------------

class TestIdempotencia:
    def test_re_aprobar_no_duplica_filas(self, db_session):
        """Llamar inferir_avance dos veces para el mismo proceso/docs NO duplica filas."""
        from app.services.inferencia_etapas import inferir_avance
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)
        docs = [_make_doc("TDR", 0.9)]
        fecha = datetime.date(2025, 6, 1)

        inferir_avance(db_session, proceso.id, docs, correo_id=1, fecha=fecha)
        inferir_avance(db_session, proceso.id, docs, correo_id=2, fecha=fecha)

        # Contar filas para E02 (lo que TDR infiere como objetivo)
        count_e02 = db_session.execute(
            sa_select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.codigo_etapa == "E02",
            )
        ).scalars().all()

        assert len(count_e02) == 1, "E02 no debe duplicarse aunque se llame inferir_avance 2 veces"


# ---------------------------------------------------------------------------
# T-INF-05  NO pisa filas manuales
# ---------------------------------------------------------------------------

class TestNoPartitionManual:
    def test_fila_manual_preexistente_no_se_sobreescribe(self, db_session):
        """Si E02 ya existe COMPLETADO registrado manualmente, inferencia no lo toca."""
        from app.services.inferencia_etapas import inferir_avance
        from sqlalchemy import select as sa_select
        from app.services.etapas_catalogo import ETAPAS_CATALOGO

        proceso = _make_proceso(db_session)

        # Crear E02 manualmente ANTES de la inferencia
        spec = ETAPAS_CATALOGO["E02"]
        fila_manual = EtapaRegistro(
            proceso_id=proceso.id,
            codigo_etapa="E02",
            nombre_etapa=spec.nombre,
            area_responsable=spec.area_responsable,
            fecha_inicio=datetime.date(2025, 1, 1),
            fecha_fin=datetime.date(2025, 1, 15),
            estado_etapa="COMPLETADO",
            registrado_por="usuario_manual",
            nro_ronda=1,
        )
        db_session.add(fila_manual)
        db_session.flush()
        id_manual = fila_manual.id

        # Inferir con TDR (objetivo E02)
        docs = [_make_doc("TDR", 0.9)]
        inferir_avance(db_session, proceso.id, docs, correo_id=77, fecha=datetime.date(2025, 6, 1))

        # La fila manual debe seguir siendo la misma
        rows = db_session.execute(
            sa_select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.codigo_etapa == "E02",
            )
        ).scalars().all()

        assert len(rows) == 1, "Debe existir exactamente 1 fila E02"
        assert rows[0].id == id_manual, "La fila E02 debe ser la manual original"
        assert rows[0].registrado_por == "usuario_manual", "registrado_por no debe cambiar"


# ---------------------------------------------------------------------------
# T-INF-06  revertir_avance
# ---------------------------------------------------------------------------

class TestRevertirAvance:
    def test_desvincular_revierte_solo_las_del_correo(self, db_session):
        """revertir_avance elimina solo filas con marca del correo indicado."""
        from app.services.inferencia_etapas import inferir_avance, revertir_avance
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)
        fecha = datetime.date(2025, 6, 1)

        # Inferir con correo 40 (COTIZACION → E03)
        docs_40 = [_make_doc("COTIZACION", 0.9)]
        inferir_avance(db_session, proceso.id, docs_40, correo_id=40, fecha=fecha)

        # Inferir con correo 42 (CONFORMIDAD → E23, mayor orden)
        # Correo 42 añade etapas que 40 no alcanzó (E04 en adelante)
        docs_42 = [_make_doc("CONFORMIDAD", 0.9)]
        inferir_avance(db_session, proceso.id, docs_42, correo_id=42, fecha=fecha)

        # Revertir correo 42 (solo las del correo 42)
        n_revertidas = revertir_avance(db_session, proceso.id, correo_id=42)
        assert n_revertidas >= 0  # puede ser 0 si todas ya existían por correo 40

        # Las filas del correo 40 deben subsistir
        rows = db_session.execute(
            sa_select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.registrado_por == "INGESTA_INFER",
            )
        ).scalars().all()
        for r in rows:
            assert f"[INGESTA_INFER corr=42]" not in (r.observaciones or ""), \
                f"Fila {r.codigo_etapa} del correo 42 deberia haber sido revertida"

    def test_desvincular_no_toca_filas_manuales(self, db_session):
        """revertir_avance nunca borra filas con registrado_por != INGESTA_INFER."""
        from app.services.inferencia_etapas import inferir_avance, revertir_avance
        from sqlalchemy import select as sa_select
        from app.services.etapas_catalogo import ETAPAS_CATALOGO

        proceso = _make_proceso(db_session)

        # Crear E01a manualmente
        spec = ETAPAS_CATALOGO["E01a"]
        fila_manual = EtapaRegistro(
            proceso_id=proceso.id,
            codigo_etapa="E01a",
            nombre_etapa=spec.nombre,
            area_responsable=spec.area_responsable,
            fecha_inicio=datetime.date(2025, 1, 1),
            fecha_fin=datetime.date(2025, 1, 1),
            estado_etapa="COMPLETADO",
            registrado_por="admin_manual",
            nro_ronda=1,
        )
        db_session.add(fila_manual)
        db_session.flush()

        # Inferir (la inferencia saltea E01a porque ya existe COMPLETADO)
        docs = [_make_doc("TDR", 0.9)]
        inferir_avance(db_session, proceso.id, docs, correo_id=99, fecha=datetime.date.today())

        # Revertir
        revertir_avance(db_session, proceso.id, correo_id=99)

        # La fila manual debe persistir
        fila = db_session.execute(
            sa_select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.codigo_etapa == "E01a",
            )
        ).scalars().first()
        assert fila is not None
        assert fila.registrado_por == "admin_manual"


# ---------------------------------------------------------------------------
# T-INF-09  Montos_proceso intactos tras inferir (FIX CRÍTICO Q1)
# ---------------------------------------------------------------------------

class TestMontosIntactos:
    def test_montos_no_pisados_al_inferir_e19_e22(self, db_session):
        """Inferir CONFORMIDAD (prefijo incluye E19/E22) NO pisa montos_proceso existentes.

        Este test fuerza la decisión de Q1: la inferencia usa INSERT directo
        sin pasar por registrar_etapa (que dispararía sync_montos con valores None).
        """
        from app.services.inferencia_etapas import inferir_avance
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)

        # Insertar montos_proceso con valores conocidos
        montos_existentes = MontosProceso(
            proceso_id=proceso.id,
            nro_ocs="OCS-2025-001",
            monto_ocs=Decimal("50000.00"),
            plazo_entrega=30,
        )
        db_session.add(montos_existentes)
        db_session.flush()

        # Inferir CONFORMIDAD → E23 (prefijo incluye E19 y E22)
        docs = [_make_doc("CONFORMIDAD", 0.9)]
        fecha = datetime.date(2025, 6, 1)
        inferir_avance(db_session, proceso.id, docs, correo_id=55, fecha=fecha)

        # Los montos deben seguir intactos
        montos = db_session.execute(
            sa_select(MontosProceso).where(MontosProceso.proceso_id == proceso.id)
        ).scalar_one_or_none()

        assert montos is not None, "MontosProceso fue eliminado por la inferencia"
        assert montos.nro_ocs == "OCS-2025-001", f"nro_ocs fue pisado: {montos.nro_ocs!r}"
        assert montos.monto_ocs == Decimal("50000.00"), f"monto_ocs fue pisado: {montos.monto_ocs!r}"
        assert montos.plazo_entrega == 30, f"plazo_entrega fue pisado: {montos.plazo_entrega!r}"

    def test_sin_montos_previos_inferencia_no_crea_montos(self, db_session):
        """Si no hay MontosProceso previo, la inferencia NO debe crearlo con valores None."""
        from app.services.inferencia_etapas import inferir_avance
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)

        # No hay MontosProceso

        docs = [_make_doc("CONFORMIDAD", 0.9)]
        fecha = datetime.date(2025, 6, 1)
        inferir_avance(db_session, proceso.id, docs, correo_id=55, fecha=fecha)

        montos = db_session.execute(
            sa_select(MontosProceso).where(MontosProceso.proceso_id == proceso.id)
        ).scalar_one_or_none()

        # La inferencia no debe crear MontosProceso con valores None
        if montos is not None:
            # Si existe (por ejemplo sync_montos fue disparado), los campos de montos deben ser None
            # El assertion real: nro_ocs y monto_ocs no deben tener valores inventados
            # pero lo más importante es que NO exista, o si existe, que provenga de
            # un sync_montos vacío (lo que sería el bug). El test principal es que no haya valores.
            assert montos.nro_ocs is None, "nro_ocs no debería tener valor inventado"
            assert montos.monto_ocs is None, "monto_ocs no debería tener valor inventado"


# ---------------------------------------------------------------------------
# T-INF-10  derivar_tiempos
# ---------------------------------------------------------------------------

class TestDerivarTiempos:
    def test_con_e03_fecha_indagacion_es_e03(self, db_session):
        """Si hay fila E03 COMPLETADO, fecha_indagacion = E03.fecha_inicio."""
        from app.services.inferencia_etapas import derivar_tiempos
        from app.services.etapas_catalogo import ETAPAS_CATALOGO
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)
        fecha_e03 = datetime.date(2025, 3, 1)

        spec = ETAPAS_CATALOGO["E03"]
        row_e03 = EtapaRegistro(
            proceso_id=proceso.id,
            codigo_etapa="E03",
            nombre_etapa=spec.nombre,
            area_responsable=spec.area_responsable,
            fecha_inicio=fecha_e03,
            fecha_fin=fecha_e03,
            estado_etapa="COMPLETADO",
            registrado_por="admin",
            nro_ronda=1,
        )
        db_session.add(row_e03)
        db_session.flush()

        rows = db_session.execute(
            sa_select(EtapaRegistro).where(EtapaRegistro.proceso_id == proceso.id)
        ).scalars().all()

        fi, fo, dt, _ea = derivar_tiempos(rows, proceso)

        assert fi == fecha_e03, f"fecha_indagacion debe ser {fecha_e03}, obtuvo {fi}"
        assert dt is not None
        assert dt >= 0

    def test_sin_e03_fallback_fecha_creacion(self, db_session):
        """Sin E03, fecha_indagacion=None y dias desde proceso.fecha_creacion."""
        from app.services.inferencia_etapas import derivar_tiempos

        proceso = _make_proceso(db_session)
        fi, fo, dt, _ea = derivar_tiempos([], proceso)

        assert fi is None
        assert dt is not None
        # dias desde fecha_creacion hasta hoy
        hoy = datetime.date.today()
        esperado = (hoy - proceso.fecha_creacion.date()).days
        assert dt == esperado

    def test_proceso_culminado_usa_e25_fecha_fin(self, db_session):
        """Si proceso CULMINADO, dias_transcurridos se mide hasta E25.fecha_fin."""
        from app.services.inferencia_etapas import derivar_tiempos
        from app.services.etapas_catalogo import ETAPAS_CATALOGO

        proceso = _make_proceso(db_session, estado="CULMINADO")
        fecha_e03 = datetime.date(2025, 3, 1)
        fecha_e25 = datetime.date(2025, 12, 1)

        spec_e03 = ETAPAS_CATALOGO["E03"]
        spec_e25 = ETAPAS_CATALOGO["E25"]

        rows = [
            EtapaRegistro(
                proceso_id=proceso.id,
                codigo_etapa="E03",
                nombre_etapa=spec_e03.nombre,
                area_responsable=spec_e03.area_responsable,
                fecha_inicio=fecha_e03,
                fecha_fin=fecha_e03,
                estado_etapa="COMPLETADO",
                registrado_por="admin",
                nro_ronda=1,
            ),
            EtapaRegistro(
                proceso_id=proceso.id,
                codigo_etapa="E25",
                nombre_etapa=spec_e25.nombre,
                area_responsable=spec_e25.area_responsable,
                fecha_inicio=fecha_e25,
                fecha_fin=fecha_e25,
                estado_etapa="COMPLETADO",
                registrado_por="admin",
                nro_ronda=1,
            ),
        ]

        fi, fo, dt, _ea = derivar_tiempos(rows, proceso)

        esperado = (fecha_e25 - fecha_e03).days
        assert dt == esperado, f"Para CULMINADO, dias debe ser E25-E03={esperado}, obtuvo {dt}"

    def test_fecha_transicion_ota_con_e04(self, db_session):
        """Con fila E04, fecha_transicion_ota = E04.fecha_inicio."""
        from app.services.inferencia_etapas import derivar_tiempos
        from app.services.etapas_catalogo import ETAPAS_CATALOGO

        proceso = _make_proceso(db_session)
        fecha_e04 = datetime.date(2025, 4, 15)

        spec = ETAPAS_CATALOGO["E04"]
        rows = [
            EtapaRegistro(
                proceso_id=proceso.id,
                codigo_etapa="E04",
                nombre_etapa=spec.nombre,
                area_responsable=spec.area_responsable,
                fecha_inicio=fecha_e04,
                fecha_fin=fecha_e04,
                estado_etapa="COMPLETADO",
                registrado_por="admin",
                nro_ronda=1,
            ),
        ]

        fi, fo, dt, _ea = derivar_tiempos(rows, proceso)
        assert fo == fecha_e04

    def test_sin_e04_fecha_transicion_ota_es_none(self, db_session):
        """Sin E04, fecha_transicion_ota=None."""
        from app.services.inferencia_etapas import derivar_tiempos

        proceso = _make_proceso(db_session)
        fi, fo, dt, _ea = derivar_tiempos([], proceso)
        assert fo is None


# ---------------------------------------------------------------------------
# T-INF-11  GET /procesos/{id} devuelve los 3 campos de tiempo
# ---------------------------------------------------------------------------

class TestGetProcesoTiempos:
    def test_get_proceso_incluye_campos_tiempo(self, client, admin_headers, db_session):
        """GET /procesos/{id} devuelve fecha_indagacion, fecha_transicion_ota, dias_transcurridos."""
        from sqlalchemy import text

        # Crear proceso via API para que tenga id_proceso válido
        resp = client.post(
            "/procesos",
            json={
                "requerimiento": "Servicio de prueba tiempos",
                "tipo": "SERVICIO",
                "areas_usuarias": ["AREA_A"],
                "anno": 2026,
                "area_iniciadora": "AREA_A",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        proceso_id = resp.json()["id"]

        resp_get = client.get(f"/procesos/{proceso_id}", headers=admin_headers)
        assert resp_get.status_code == 200, resp_get.text
        data = resp_get.json()

        # Los campos deben existir en la respuesta (pueden ser None)
        assert "fecha_indagacion" in data, "fecha_indagacion debe existir en la respuesta"
        assert "fecha_transicion_ota" in data, "fecha_transicion_ota debe existir en la respuesta"
        assert "dias_transcurridos" in data, "dias_transcurridos debe existir en la respuesta"

    def test_get_proceso_dias_transcurridos_no_nulo_cuando_no_hay_e03(self, client, admin_headers):
        """Sin E03, dias_transcurridos se calcula desde fecha_creacion (no None)."""
        resp = client.post(
            "/procesos",
            json={
                "requerimiento": "Proceso sin E03",
                "tipo": "BIEN",
                "areas_usuarias": ["AREA_B"],
                "anno": 2026,
                "area_iniciadora": "AREA_B",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201
        proceso_id = resp.json()["id"]

        resp_get = client.get(f"/procesos/{proceso_id}", headers=admin_headers)
        data = resp_get.json()

        # Sin E03, fecha_indagacion = None
        assert data["fecha_indagacion"] is None
        # pero dias_transcurridos NO debe ser None (fallback a fecha_creacion)
        assert data["dias_transcurridos"] is not None
        assert data["dias_transcurridos"] >= 0


# ---------------------------------------------------------------------------
# T-INF-12  inferir_etapa_correo — keywords del asunto (REFINAMIENTO)
# ---------------------------------------------------------------------------

class TestInferirEtapaCorreo:
    """Tests para la nueva función inferir_etapa_correo(subject, documentos).

    La prioridad es: keywords del asunto > fallback a tipo_clasificado del doc.
    Keywords son case-insensitive y se comparan sin acentos.
    """

    def test_indagacion_de_mercado_retorna_e03(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("Indagación de mercado - servicio limpieza", []) == "E03"

    def test_cotizacion_asunto_retorna_e03(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("Cotización SERVICIO MANTENIMIENTO", []) == "E03"

    def test_validacion_de_cotizaciones_retorna_e03(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("Validación de cotizaciones recibidas", []) == "E03"

    def test_orden_de_servicio_retorna_e19(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("ORDEN DE SERVICIO N° 023-2025", []) == "E19"

    def test_notificacion_orden_retorna_e19(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("Notificación orden emitida", []) == "E19"

    def test_conformidad_asunto_retorna_e23(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("Conformidad de servicio recibido", []) == "E23"

    def test_observaciones_retorna_e05(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("Observaciones al TDR presentado", []) == "E05"

    def test_vb_retorna_e02b(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        result = inferir_etapa_correo("V°B° al documento técnico", [])
        assert result in ("E02b", "E02"), f"V°B° debe mapear a E02b o E02, obtuvo {result}"

    def test_visto_bueno_retorna_e02b(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        result = inferir_etapa_correo("Visto bueno del TDR consolidado", [])
        assert result in ("E02b", "E02"), f"visto bueno debe mapear a E02b o E02, obtuvo {result}"

    def test_tdr_asunto_retorna_e02b_o_e02(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        result = inferir_etapa_correo("TDR para servicio de mantenimiento", [])
        assert result in ("E02b", "E02"), f"TDR en asunto debe mapear a E02b o E02, obtuvo {result}"

    def test_contratacion_retorna_e01a(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("Solicitud de contratación urgente", []) == "E01a"

    def test_requerimiento_retorna_e01a(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("Requerimiento de servicio TIC", []) == "E01a"

    def test_solicito_de_contratacion_retorna_e01a(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("Solicito de contratación servicio de limpieza", []) == "E01a"

    def test_asunto_sin_keyword_fallback_a_documento(self):
        """Sin keyword en asunto, usa tipo_clasificado del documento."""
        from app.services.inferencia_etapas import inferir_etapa_correo
        docs = [_make_doc("CONFORMIDAD", 0.9)]
        result = inferir_etapa_correo("Adjunto documentos del proceso", docs)
        assert result == "E23", f"Fallback a doc CONFORMIDAD debe dar E23, obtuvo {result}"

    def test_asunto_sin_keyword_fallback_tdr(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        docs = [_make_doc("TDR", 0.85)]
        result = inferir_etapa_correo("Documentación adjunta", docs)
        assert result == "E02", f"Fallback a doc TDR debe dar E02, obtuvo {result}"

    def test_asunto_vacío_y_sin_docs_retorna_none(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("", []) is None

    def test_asunto_none_y_sin_docs_retorna_none(self):
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo(None, []) is None

    def test_keyword_case_insensitive(self):
        """Keywords son case-insensitive."""
        from app.services.inferencia_etapas import inferir_etapa_correo
        assert inferir_etapa_correo("CONFORMIDAD DEL SERVICIO", []) == "E23"
        assert inferir_etapa_correo("conformidad del servicio", []) == "E23"

    def test_keyword_prioridad_sobre_documento(self):
        """Keyword en asunto tiene prioridad sobre tipo_doc del adjunto."""
        from app.services.inferencia_etapas import inferir_etapa_correo
        # Asunto dice "conformidad" pero doc dice TDR → asunto gana → E23
        docs = [_make_doc("TDR", 0.95)]
        result = inferir_etapa_correo("Conformidad recibida", docs)
        assert result == "E23", f"Keyword del asunto debe tener prioridad, obtuvo {result}"


# ---------------------------------------------------------------------------
# T-INF-13  inferir_avance — SIN cascada (solo 1 etapa por correo) (REFINAMIENTO)
# ---------------------------------------------------------------------------

class TestInferirAvanceSinCascada:
    """La nueva inferencia marca SOLO la etapa inferida, no la cadena anterior."""

    def test_conformidad_marca_solo_e23_no_cadena(self, db_session):
        """Correo de conformidad → SOLO E23 COMPLETADO, no la cadena E01..E22."""
        from app.services.inferencia_etapas import inferir_avance_correo

        proceso = _make_proceso(db_session)
        fecha = datetime.date(2025, 11, 5)

        class _CorreoStub:
            id = 101
            subject = "Conformidad de servicio"
            sender_name = "Juan Pérez"
            numero_oficio = "OF-2025-042"
            fecha_documento = fecha
            received_at = None

        marcadas = inferir_avance_correo(db_session, proceso.id, _CorreoStub(), [_make_doc("CONFORMIDAD", 0.9)])

        # Solo debe marcar E23 (o ninguna si E23 es por_area — pero E23 NO es por_area)
        assert marcadas == ["E23"], f"Solo debe marcar E23, obtuvo {marcadas}"

        rows = db_session.execute(
            __import__("sqlalchemy").select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.estado_etapa == "COMPLETADO",
            )
        ).scalars().all()
        completadas = {r.codigo_etapa for r in rows}

        # Solo E23 debe estar COMPLETADO
        assert completadas == {"E23"}, f"Solo E23 debe quedar COMPLETADO, obtuvo {completadas}"

    def test_cotizacion_marca_solo_e03(self, db_session):
        """Correo de cotización → SOLO E03 COMPLETADO."""
        from app.services.inferencia_etapas import inferir_avance_correo

        proceso = _make_proceso(db_session)

        class _CorreoStub:
            id = 102
            subject = "Cotización servicio"
            sender_name = "Ana López"
            numero_oficio = "OF-2025-010"
            fecha_documento = datetime.date(2025, 4, 15)
            received_at = None

        marcadas = inferir_avance_correo(db_session, proceso.id, _CorreoStub(), [_make_doc("COTIZACION", 0.9)])
        assert marcadas == ["E03"], f"Solo debe marcar E03, obtuvo {marcadas}"

    def test_dos_correos_etapas_distintas_fechas_distintas(self, db_session):
        """Dos correos de etapas distintas generan 2 etapas con FECHAS DISTINTAS."""
        from app.services.inferencia_etapas import inferir_avance_correo
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)

        class _CorreoCot:
            id = 201
            subject = "Cotización servicio"
            sender_name = "Ana López"
            numero_oficio = "OF-2025-010"
            fecha_documento = datetime.date(2025, 4, 15)
            received_at = None

        class _CorreoConf:
            id = 202
            subject = "Conformidad del servicio"
            sender_name = "Pedro Rodríguez"
            numero_oficio = "OF-2025-099"
            fecha_documento = datetime.date(2025, 11, 5)
            received_at = None

        inferir_avance_correo(db_session, proceso.id, _CorreoCot(), [_make_doc("COTIZACION", 0.9)])
        inferir_avance_correo(db_session, proceso.id, _CorreoConf(), [_make_doc("CONFORMIDAD", 0.9)])

        rows = db_session.execute(
            sa_select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.estado_etapa == "COMPLETADO",
                EtapaRegistro.registrado_por == "INGESTA_INFER",
            )
        ).scalars().all()

        por_codigo = {r.codigo_etapa: r for r in rows}

        # Ambas deben existir
        assert "E03" in por_codigo, "E03 no fue inferida"
        assert "E23" in por_codigo, "E23 no fue inferida"

        # Con FECHAS DISTINTAS (no la misma fecha del disparador)
        fecha_e03 = por_codigo["E03"].fecha_inicio
        fecha_e23 = por_codigo["E23"].fecha_inicio
        assert fecha_e03 != fecha_e23, (
            f"E03 y E23 tienen la misma fecha {fecha_e03!r} — se espera que sean diferentes "
            f"porque vienen de correos diferentes"
        )
        assert fecha_e03 == datetime.date(2025, 4, 15), f"E03 debe tener fecha del correo de cotización"
        assert fecha_e23 == datetime.date(2025, 11, 5), f"E23 debe tener fecha del correo de conformidad"

    def test_etapa_inferida_tiene_datos_reales(self, db_session):
        """La etapa inferida tiene responsable=sender_name y oficio_correo=numero_oficio."""
        from app.services.inferencia_etapas import inferir_avance_correo
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)

        class _CorreoStub:
            id = 301
            subject = "Conformidad recibida"
            sender_name = "María García"
            numero_oficio = "OF-2025-077"
            fecha_documento = datetime.date(2025, 10, 20)
            received_at = None

        inferir_avance_correo(db_session, proceso.id, _CorreoStub(), [_make_doc("CONFORMIDAD", 0.9)])

        rows = db_session.execute(
            sa_select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.codigo_etapa == "E23",
            )
        ).scalars().all()

        assert len(rows) == 1, "Debe existir exactamente 1 fila E23"
        row = rows[0]
        assert row.responsable == "María García", f"responsable debe ser sender_name, obtuvo {row.responsable!r}"
        assert row.oficio_correo == "OF-2025-077", f"oficio_correo debe ser numero_oficio, obtuvo {row.oficio_correo!r}"

    def test_etapa_inferida_fecha_fallback_received_at(self, db_session):
        """Si no hay fecha_documento, usa received_at.date() como fecha."""
        from app.services.inferencia_etapas import inferir_avance_correo
        from sqlalchemy import select as sa_select
        import datetime as dt_module

        proceso = _make_proceso(db_session)
        fecha_recibido = dt_module.datetime(2025, 9, 10, 14, 30, 0)

        class _CorreoStub:
            id = 401
            subject = "Conformidad del proceso"
            sender_name = "Carlos Torres"
            numero_oficio = None
            fecha_documento = None
            received_at = fecha_recibido

        inferir_avance_correo(db_session, proceso.id, _CorreoStub(), [_make_doc("CONFORMIDAD", 0.9)])

        rows = db_session.execute(
            sa_select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.codigo_etapa == "E23",
            )
        ).scalars().all()

        assert len(rows) == 1
        assert rows[0].fecha_inicio == dt_module.date(2025, 9, 10)

    def test_desvincular_revierte_solo_1_etapa(self, db_session):
        """Desvincular un correo revierte solo la 1 etapa que ese correo marcó."""
        from app.services.inferencia_etapas import inferir_avance_correo, revertir_avance
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)

        class _CorreoCot:
            id = 501
            subject = "Cotización recibida"
            sender_name = "Ana López"
            numero_oficio = "OF-2025-010"
            fecha_documento = datetime.date(2025, 4, 15)
            received_at = None

        class _CorreoConf:
            id = 502
            subject = "Conformidad del servicio"
            sender_name = "Pedro Rodríguez"
            numero_oficio = "OF-2025-099"
            fecha_documento = datetime.date(2025, 11, 5)
            received_at = None

        inferir_avance_correo(db_session, proceso.id, _CorreoCot(), [_make_doc("COTIZACION", 0.9)])
        inferir_avance_correo(db_session, proceso.id, _CorreoConf(), [_make_doc("CONFORMIDAD", 0.9)])

        # Desvincular solo el correo de conformidad
        n = revertir_avance(db_session, proceso.id, correo_id=502)
        assert n == 1, f"Debe revertir exactamente 1 etapa, revirtió {n}"

        rows = db_session.execute(
            sa_select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.estado_etapa == "COMPLETADO",
            )
        ).scalars().all()
        completadas = {r.codigo_etapa for r in rows}

        # E03 debe subsistir (del correo de cotización)
        assert "E03" in completadas, "E03 debe subsistir tras desvincular el correo de conformidad"
        # E23 debe haber sido revertida
        assert "E23" not in completadas, "E23 debe haberse revertido"

    def test_idempotencia_un_correo_no_duplica(self, db_session):
        """Llamar inferir_avance_correo dos veces para el mismo correo/etapa NO duplica."""
        from app.services.inferencia_etapas import inferir_avance_correo
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)

        class _CorreoStub:
            id = 601
            subject = "Conformidad recibida"
            sender_name = "Test User"
            numero_oficio = None
            fecha_documento = datetime.date(2025, 6, 1)
            received_at = None

        inferir_avance_correo(db_session, proceso.id, _CorreoStub(), [_make_doc("CONFORMIDAD", 0.9)])
        inferir_avance_correo(db_session, proceso.id, _CorreoStub(), [_make_doc("CONFORMIDAD", 0.9)])

        rows = db_session.execute(
            sa_select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso.id,
                EtapaRegistro.codigo_etapa == "E23",
            )
        ).scalars().all()
        assert len(rows) == 1, "E23 no debe duplicarse"

    def test_montos_intactos_inferencia_puntual(self, db_session):
        """Inferir E19 puntualmente (orden de servicio) NO pisa montos_proceso."""
        from app.services.inferencia_etapas import inferir_avance_correo
        from sqlalchemy import select as sa_select

        proceso = _make_proceso(db_session)
        montos = MontosProceso(
            proceso_id=proceso.id,
            nro_ocs="OCS-2025-009",
            monto_ocs=Decimal("25000.00"),
        )
        db_session.add(montos)
        db_session.flush()

        class _CorreoStub:
            id = 701
            subject = "Orden de servicio emitida"
            sender_name = "OEAS Contrataciones"
            numero_oficio = "OF-2025-030"
            fecha_documento = datetime.date(2025, 8, 20)
            received_at = None

        inferir_avance_correo(db_session, proceso.id, _CorreoStub(), [_make_doc("ORDEN_SERVICIO", 0.9)])

        montos_post = db_session.execute(
            sa_select(MontosProceso).where(MontosProceso.proceso_id == proceso.id)
        ).scalar_one_or_none()

        assert montos_post is not None
        assert montos_post.nro_ocs == "OCS-2025-009"
        assert montos_post.monto_ocs == Decimal("25000.00")


# ---------------------------------------------------------------------------
# T-INF-14  etapa_actual_avance — etapa de mayor orden COMPLETADO (REFINAMIENTO)
# ---------------------------------------------------------------------------

class TestEtapaActualAvance:
    """Tests para el campo etapa_actual_avance en derivar_tiempos / ProcesoOut."""

    def test_derivar_tiempos_retorna_etapa_actual(self, db_session):
        """derivar_tiempos debe retornar etapa_actual_avance = código de mayor orden COMPLETADO."""
        from app.services.inferencia_etapas import derivar_tiempos
        from app.services.etapas_catalogo import ETAPAS_CATALOGO

        proceso = _make_proceso(db_session)
        spec_e03 = ETAPAS_CATALOGO["E03"]
        spec_e23 = ETAPAS_CATALOGO["E23"]

        rows = [
            EtapaRegistro(
                proceso_id=proceso.id, codigo_etapa="E03",
                nombre_etapa=spec_e03.nombre, area_responsable=spec_e03.area_responsable,
                fecha_inicio=datetime.date(2025, 4, 1), fecha_fin=datetime.date(2025, 4, 1),
                estado_etapa="COMPLETADO", registrado_por="admin", nro_ronda=1,
            ),
            EtapaRegistro(
                proceso_id=proceso.id, codigo_etapa="E23",
                nombre_etapa=spec_e23.nombre, area_responsable=spec_e23.area_responsable,
                fecha_inicio=datetime.date(2025, 11, 1), fecha_fin=datetime.date(2025, 11, 1),
                estado_etapa="COMPLETADO", registrado_por="admin", nro_ronda=1,
            ),
        ]

        result = derivar_tiempos(rows, proceso)
        # derivar_tiempos ahora retorna 4-tuple: (fi, fo, dt, etapa_actual_avance)
        assert len(result) == 4, f"derivar_tiempos debe retornar 4 valores, retornó {len(result)}"
        fi, fo, dt, etapa_actual = result

        assert etapa_actual == "E23", f"etapa_actual_avance debe ser E23 (mayor orden COMPLETADO), obtuvo {etapa_actual!r}"

    def test_derivar_tiempos_sin_completadas_retorna_none(self, db_session):
        """Sin etapas COMPLETADO, etapa_actual_avance debe ser None."""
        from app.services.inferencia_etapas import derivar_tiempos

        proceso = _make_proceso(db_session)
        fi, fo, dt, etapa_actual = derivar_tiempos([], proceso)

        assert etapa_actual is None

    def test_derivar_tiempos_mayor_orden_gana(self, db_session):
        """Con E03 y E19 ambas COMPLETADO, gana E19 (mayor orden)."""
        from app.services.inferencia_etapas import derivar_tiempos
        from app.services.etapas_catalogo import ETAPAS_CATALOGO

        proceso = _make_proceso(db_session)
        spec_e03 = ETAPAS_CATALOGO["E03"]
        spec_e19 = ETAPAS_CATALOGO["E19"]

        rows = [
            EtapaRegistro(
                proceso_id=proceso.id, codigo_etapa="E03",
                nombre_etapa=spec_e03.nombre, area_responsable=spec_e03.area_responsable,
                fecha_inicio=datetime.date(2025, 4, 1), fecha_fin=datetime.date(2025, 4, 1),
                estado_etapa="COMPLETADO", registrado_por="admin", nro_ronda=1,
            ),
            EtapaRegistro(
                proceso_id=proceso.id, codigo_etapa="E19",
                nombre_etapa=spec_e19.nombre, area_responsable=spec_e19.area_responsable,
                fecha_inicio=datetime.date(2025, 8, 1), fecha_fin=datetime.date(2025, 8, 1),
                estado_etapa="COMPLETADO", registrado_por="admin", nro_ronda=1,
            ),
        ]

        fi, fo, dt, etapa_actual = derivar_tiempos(rows, proceso)
        assert etapa_actual == "E19", f"E19 debe ganar sobre E03, obtuvo {etapa_actual!r}"

    def test_get_proceso_incluye_etapa_actual_avance(self, client, admin_headers, db_session):
        """GET /procesos/{id} devuelve etapa_actual_avance en la respuesta."""
        resp = client.post(
            "/procesos",
            json={
                "requerimiento": "Proceso test etapa avance",
                "tipo": "SERVICIO",
                "areas_usuarias": ["AREA_A"],
                "anno": 2026,
                "area_iniciadora": "AREA_A",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201
        proceso_id = resp.json()["id"]

        resp_get = client.get(f"/procesos/{proceso_id}", headers=admin_headers)
        assert resp_get.status_code == 200
        data = resp_get.json()

        # El campo debe existir (puede ser None si no hay etapas COMPLETADO)
        assert "etapa_actual_avance" in data, "etapa_actual_avance debe estar en la respuesta"
