"""Tests for etapas_service mechanics (C3a scope — no business rule tests).

Rule-blocking tests (R1-R8 409s) are NOT here — they belong to C3b.
Where a C3b test would go, a comment marks it: # C3b.

Uses the transactional db_session fixture and autouse _clean_business_tables
from conftest.py — table is clean at the start of every test.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.etapa import EtapaRegistro
from app.models.historial import HistorialCambio
from app.models.proceso import Proceso
from app.schemas.etapa import BucleCreate, EtapaCreate, EtapaUpdate
from app.services.etapas_catalogo import ORDEN_ETAPAS
from app.services.etapas_service import (
    _registrar_auditoria,
    actualizar_etapa,
    agregar_ronda_bucle,
    agrupar_etapas,
    calcular_progreso,
    registrar_etapa,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proceso(db_session, areas: list[str] | None = None) -> Proceso:
    """Insert a minimal Proceso and return it."""
    p = Proceso(
        id_proceso="2026-TST",
        requerimiento="Test proceso",
        tipo="BIEN",
        areas_usuarias=areas or ["DTDIS"],
        estado="EN PROCESO",
        anno=2026,
        creado_por="testuser",
    )
    db_session.add(p)
    db_session.flush()
    return p


def _make_etapa(
    db_session,
    proceso_id: int,
    cod: str = "E02",
    estado: str = "PENDIENTE",
    area_usuaria: str | None = None,
    monto_cert: Decimal | None = None,
    nro_ronda: int = 1,
    es_bucle: bool = False,
    fecha_envio_otpp: date | None = None,
    fecha_resp_otpp: date | None = None,
    cmn_adjunto: str | None = None,
) -> EtapaRegistro:
    """Insert a minimal EtapaRegistro and return it."""
    r = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=f"Etapa {cod}",
        area_responsable="OTIN",
        estado_etapa=estado,
        area_usuaria=area_usuaria,
        monto_cert=monto_cert,
        nro_ronda=nro_ronda,
        es_bucle=es_bucle,
        fecha_envio_otpp=fecha_envio_otpp,
        fecha_resp_otpp=fecha_resp_otpp,
        cmn_adjunto=cmn_adjunto,
        registrado_por="testuser",
    )
    db_session.add(r)
    db_session.flush()
    return r


# ---------------------------------------------------------------------------
# registrar_etapa
# ---------------------------------------------------------------------------

def test_registrar_etapa_simple(db_session):
    """POST E02 creates a row with correct fields (E01c prereq satisfied for all areas)."""
    proc = _make_proceso(db_session)
    # flujo-real-otin-v2: prereqs are E01a→E01b→E01c→E02
    _make_etapa(db_session, proc.id, cod="E01a", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01b", estado="COMPLETADO")
    _make_etapa(
        db_session, proc.id, cod="E01c",
        estado="COMPLETADO", area_usuaria="DTDIS",
    )
    payload = EtapaCreate(
        codigo_etapa="E02",
        nombre_etapa="Elaboración TDR consolidado",
        fecha_inicio=date(2026, 6, 1),
        estado_etapa="EN_CURSO",
    )
    etapa = registrar_etapa(db_session, proc.id, payload, "editor1")
    assert etapa.id is not None
    assert etapa.proceso_id == proc.id
    assert etapa.codigo_etapa == "E02"
    assert etapa.estado_etapa == "EN_CURSO"
    assert etapa.registrado_por == "editor1"
    assert etapa.nro_ronda == 1


def test_registrar_etapa_bucle_sets_es_bucle(db_session):
    """Registering E05 sets es_bucle=True from catalog (E04 prereq satisfied)."""
    proc = _make_proceso(db_session)
    # R6: E04 must be COMPLETADO for E05 to register
    _make_etapa(db_session, proc.id, cod="E04", estado="COMPLETADO")
    payload = EtapaCreate(
        codigo_etapa="E05",
        nombre_etapa="Observaciones TDR",
        motivo_bucle="Primera observación",
    )
    etapa = registrar_etapa(db_session, proc.id, payload, "editor1")
    assert etapa.es_bucle is True


def test_registrar_etapa_no_historial(db_session):
    """POST creates no historial_cambios row (audit is for PUT only)."""
    proc = _make_proceso(db_session)
    # E03 prereq chain: E01a→E01b→E01c→E02→E02b→E03
    _make_etapa(db_session, proc.id, cod="E01a", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01b", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01c", estado="COMPLETADO", area_usuaria="DTDIS")
    _make_etapa(db_session, proc.id, cod="E02", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E02b", estado="COMPLETADO")
    payload = EtapaCreate(
        codigo_etapa="E03",
        nombre_etapa="Envío indagación",
    )
    registrar_etapa(db_session, proc.id, payload, "editor1")
    count = db_session.execute(select(HistorialCambio)).scalars().all()
    assert len(count) == 0


# ---------------------------------------------------------------------------
# agregar_ronda_bucle
# ---------------------------------------------------------------------------

def test_agregar_ronda_bucle_increments_nro_ronda(db_session):
    """After inserting ronda 1, agregar_ronda_bucle creates ronda 2 (E04 prereq satisfied)."""
    proc = _make_proceso(db_session)
    # R6: E04 COMPLETADO required for E05/E06 bucles
    _make_etapa(db_session, proc.id, cod="E04", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E05", es_bucle=True, nro_ronda=1)

    payload = BucleCreate(motivo_bucle="Segunda observación")
    nueva_ronda = agregar_ronda_bucle(db_session, proc.id, "E05", payload, "editor1")

    assert nueva_ronda.nro_ronda == 2
    assert nueva_ronda.motivo_bucle == "Segunda observación"
    assert nueva_ronda.es_bucle is True


def test_agregar_ronda_bucle_from_zero(db_session):
    """When no previous rows exist, nro_ronda starts at 1 (E04 prereq satisfied)."""
    proc = _make_proceso(db_session)
    # R6: E04 COMPLETADO required for E06 bucle
    _make_etapa(db_session, proc.id, cod="E04", estado="COMPLETADO")
    payload = BucleCreate(motivo_bucle="Primera ronda")
    nueva_ronda = agregar_ronda_bucle(db_session, proc.id, "E06", payload, "editor1")
    assert nueva_ronda.nro_ronda == 1


def test_agregar_ronda_bucle_multiple_increments(db_session):
    """Three consecutive rounds increment nro_ronda correctly (E04 prereq satisfied)."""
    proc = _make_proceso(db_session)
    # R6: E04 COMPLETADO required
    _make_etapa(db_session, proc.id, cod="E04", estado="COMPLETADO")
    p = BucleCreate(motivo_bucle="x")
    r1 = agregar_ronda_bucle(db_session, proc.id, "E05", p, "u")
    r2 = agregar_ronda_bucle(db_session, proc.id, "E05", p, "u")
    r3 = agregar_ronda_bucle(db_session, proc.id, "E05", p, "u")
    assert r1.nro_ronda == 1
    assert r2.nro_ronda == 2
    assert r3.nro_ronda == 3


# ---------------------------------------------------------------------------
# actualizar_etapa + audit
# ---------------------------------------------------------------------------

def test_actualizar_etapa_changes_field(db_session):
    """PUT updates the mutable field on the row."""
    proc = _make_proceso(db_session)
    etapa = _make_etapa(db_session, proc.id, cod="E02", estado="PENDIENTE")

    payload = EtapaUpdate(estado_etapa="COMPLETADO")
    updated = actualizar_etapa(db_session, etapa, payload, "editor1")
    assert updated.estado_etapa == "COMPLETADO"
    assert updated.actualizado_por == "editor1"


def test_auditoria_on_put(db_session):
    """PUT that changes estado_etapa creates historial_cambios row."""
    proc = _make_proceso(db_session)
    etapa = _make_etapa(db_session, proc.id, cod="E02", estado="EN_CURSO")

    payload = EtapaUpdate(estado_etapa="COMPLETADO")
    actualizar_etapa(db_session, etapa, payload, "editor1")

    historial = db_session.execute(select(HistorialCambio)).scalars().all()
    assert len(historial) >= 1
    campos = [h.campo_modificado for h in historial]
    assert "estado_etapa" in campos


def test_auditoria_not_on_post(db_session):
    """POST (registrar_etapa) does NOT write historial_cambios (prereqs satisfied)."""
    proc = _make_proceso(db_session)
    # flujo-real-otin-v2: prereq chain E01a→E01b→E01c→E02
    _make_etapa(db_session, proc.id, cod="E01a", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01b", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01c", estado="COMPLETADO", area_usuaria="DTDIS")
    payload = EtapaCreate(codigo_etapa="E02", nombre_etapa="TDR")
    registrar_etapa(db_session, proc.id, payload, "editor1")

    historial = db_session.execute(select(HistorialCambio)).scalars().all()
    assert len(historial) == 0


def test_auditoria_multiple_fields(db_session):
    """PUT changing two fields produces two historial rows."""
    proc = _make_proceso(db_session)
    etapa = _make_etapa(db_session, proc.id, cod="E03", estado="PENDIENTE")

    payload = EtapaUpdate(estado_etapa="EN_CURSO", responsable="Juan Perez")
    actualizar_etapa(db_session, etapa, payload, "editor1")

    historial = db_session.execute(select(HistorialCambio)).scalars().all()
    assert len(historial) == 2


# ---------------------------------------------------------------------------
# Per-area rows (E11 / E24)
# ---------------------------------------------------------------------------

def test_per_area_rows_e11_grouped(db_session):
    """Two E11 rows for different areas appear in grouped filas."""
    proc = _make_proceso(db_session, areas=["DTDIS", "GOBERNANZA"])
    _make_etapa(
        db_session, proc.id, cod="E11",
        area_usuaria="DTDIS", monto_cert=Decimal("80000"),
    )
    _make_etapa(
        db_session, proc.id, cod="E11",
        area_usuaria="GOBERNANZA", monto_cert=Decimal("70000"),
    )

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    grupos = agrupar_etapas(list(rows))
    e11_group = next(g for g in grupos if g.cod == "E11")

    assert len(e11_group.filas) == 2
    assert e11_group.monto_total == Decimal("150000")


def test_per_area_rows_e11_monto_total_none_when_no_montos(db_session):
    """E11 rows without monto_cert → monto_total is None."""
    proc = _make_proceso(db_session, areas=["DTDIS"])
    _make_etapa(db_session, proc.id, cod="E11", area_usuaria="DTDIS")

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    grupos = agrupar_etapas(list(rows))
    e11_group = next(g for g in grupos if g.cod == "E11")
    assert e11_group.monto_total is None


# ---------------------------------------------------------------------------
# calcular_progreso
# ---------------------------------------------------------------------------

def test_calcular_progreso_vacio():
    """No rows → etapa_actual='E01a', porcentaje=0, completadas=0, total=26."""
    progreso = calcular_progreso([])
    assert progreso.etapa_actual == "E01a"
    assert progreso.porcentaje == 0
    assert progreso.completadas == 0
    assert progreso.total == 26


def test_calcular_progreso_una_completada():
    """One non-bucle stage COMPLETADO → completadas=1, porcentaje≈3.85."""
    row = EtapaRegistro()
    row.codigo_etapa = "E01a"
    row.estado_etapa = "COMPLETADO"
    row.nro_ronda = 1
    row.es_bucle = False

    progreso = calcular_progreso([row])
    assert progreso.completadas == 1
    # porcentaje = 1/26 * 100 ≈ 3.8 (rounded to 1 decimal as per calcular_progreso impl)
    assert progreso.porcentaje > 0
    assert progreso.porcentaje < 5
    assert progreso.etapa_actual == "E01b"


def test_calcular_progreso_parcial(db_session):
    """E01a COMPLETADO, E01b EN_CURSO → completadas=1, etapa_actual=E01b."""
    proc = _make_proceso(db_session)
    _make_etapa(db_session, proc.id, cod="E01a", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01b", estado="EN_CURSO")

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    progreso = calcular_progreso(list(rows))
    assert progreso.completadas == 1
    assert progreso.etapa_actual == "E01b"


def test_calcular_progreso_bucles_excluidos(db_session):
    """E05 (bucle) COMPLETADO does not raise denominator above 26."""
    proc = _make_proceso(db_session)
    # Complete all non-bucle stages up to E04 (new chain: E01a, E01b, E01c, E02, E02b, E03, E04)
    for cod in ["E01a", "E01b", "E02", "E02b", "E03", "E04"]:
        _make_etapa(db_session, proc.id, cod=cod, estado="COMPLETADO")
    # E01c is por_area — add one area row
    _make_etapa(db_session, proc.id, cod="E01c", estado="COMPLETADO", area_usuaria="DTDIS")
    # E05 bucle COMPLETADO
    _make_etapa(db_session, proc.id, cod="E05", estado="COMPLETADO", es_bucle=True)

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    progreso = calcular_progreso(list(rows))
    # 7 non-bucle stages done (E01a, E01b, E01c, E02, E02b, E03, E04). E05 excluded.
    assert progreso.completadas == 7
    assert progreso.total == 26


def test_calcular_progreso_omitido_no_cuenta(db_session):
    """OMITIDO rows are not counted as COMPLETADO."""
    proc = _make_proceso(db_session)
    _make_etapa(db_session, proc.id, cod="E02", estado="OMITIDO")

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    progreso = calcular_progreso(list(rows))
    assert progreso.completadas == 0
    assert progreso.etapa_actual == "E01a"


def test_calcular_progreso_bucle_ultima_ronda(db_session):
    """For loop stages, only the LAST ronda's estado is used."""
    proc = _make_proceso(db_session)
    # ronda 1 = COMPLETADO, ronda 2 = PENDIENTE → consolidated = PENDIENTE
    _make_etapa(
        db_session, proc.id, cod="E05",
        estado="COMPLETADO", es_bucle=True, nro_ronda=1,
    )
    _make_etapa(
        db_session, proc.id, cod="E05",
        estado="PENDIENTE", es_bucle=True, nro_ronda=2,
    )

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    progreso = calcular_progreso(list(rows))
    # E05 last ronda is PENDIENTE → not counted (also excluded from denominator anyway)
    # etapa_actual is first non-COMPLETADO in order
    assert progreso.etapa_actual == "E01a"


def test_calcular_progreso_por_area_todas_completadas(db_session):
    """por_area stage (E01c): COMPLETADO only when ALL area rows are COMPLETADO."""
    proc = _make_proceso(db_session, areas=["DTDIS", "GOBERNANZA"])
    _make_etapa(db_session, proc.id, cod="E01a", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01b", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01c", estado="COMPLETADO", area_usuaria="DTDIS")
    _make_etapa(db_session, proc.id, cod="E01c", estado="COMPLETADO", area_usuaria="GOBERNANZA")

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    progreso = calcular_progreso(list(rows))
    assert progreso.completadas == 3  # E01a, E01b, E01c each count once
    assert progreso.etapa_actual == "E02"


def test_calcular_progreso_por_area_una_pendiente(db_session):
    """por_area stage (E01c): not COMPLETADO when at least one area is PENDIENTE."""
    proc = _make_proceso(db_session, areas=["DTDIS", "GOBERNANZA"])
    _make_etapa(db_session, proc.id, cod="E01a", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01b", estado="COMPLETADO")
    _make_etapa(db_session, proc.id, cod="E01c", estado="COMPLETADO", area_usuaria="DTDIS")
    _make_etapa(db_session, proc.id, cod="E01c", estado="PENDIENTE", area_usuaria="GOBERNANZA")

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    progreso = calcular_progreso(list(rows))
    assert progreso.completadas == 2  # E01a and E01b count, E01c not (partial)
    assert progreso.etapa_actual == "E01c"


# ---------------------------------------------------------------------------
# agrupar_etapas — structure (contract pin per APPLY-TIME RISK #1)
# ---------------------------------------------------------------------------

def test_agrupar_etapas_returns_all_28(db_session):
    """GET grouped structure returns all 32 etapas (flujo-real-otin-v2) even with no rows."""
    proc = _make_proceso(db_session)
    grupos = agrupar_etapas([])
    assert len(grupos) == 32


def test_agrupar_etapas_orden_correcto():
    """Grouped etapas are returned in ORDEN_ETAPAS order."""
    grupos = agrupar_etapas([])
    cods = [g.cod for g in grupos]
    assert cods == ORDEN_ETAPAS


def test_agrupar_etapas_pendiente_sin_rows():
    """All stages are PENDIENTE when no rows registered."""
    grupos = agrupar_etapas([])
    assert all(g.estado == "PENDIENTE" for g in grupos)


def test_agrupar_etapas_bucle_usa_rondas(db_session):
    """Loop stages populate rondas[], not filas[]."""
    proc = _make_proceso(db_session)
    _make_etapa(db_session, proc.id, cod="E05", es_bucle=True, nro_ronda=1)
    _make_etapa(db_session, proc.id, cod="E05", es_bucle=True, nro_ronda=2)

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    grupos = agrupar_etapas(list(rows))
    e05 = next(g for g in grupos if g.cod == "E05")
    assert len(e05.rondas) == 2
    assert len(e05.filas) == 0
    # Rondas sorted by nro_ronda
    assert e05.rondas[0].nro_ronda == 1
    assert e05.rondas[1].nro_ronda == 2


def test_agrupar_etapas_e16_alerta_false_dentro_plazo(db_session):
    """E16 alerta_otpp=False when response is within 20 days."""
    proc = _make_proceso(db_session)
    envio = date(2026, 6, 1)
    respuesta = envio + timedelta(days=10)
    _make_etapa(
        db_session, proc.id, cod="E16",
        fecha_envio_otpp=envio, fecha_resp_otpp=respuesta,
    )

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    grupos = agrupar_etapas(list(rows))
    e16 = next(g for g in grupos if g.cod == "E16")
    assert e16.alerta_otpp is False


def test_agrupar_etapas_e16_alerta_true_respuesta_tardia(db_session):
    """E16 alerta_otpp=True when fecha_resp_otpp - fecha_envio_otpp > 20 days."""
    proc = _make_proceso(db_session)
    envio = date(2026, 6, 1)
    respuesta = envio + timedelta(days=21)
    _make_etapa(
        db_session, proc.id, cod="E16",
        fecha_envio_otpp=envio, fecha_resp_otpp=respuesta,
    )

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    grupos = agrupar_etapas(list(rows))
    e16 = next(g for g in grupos if g.cod == "E16")
    assert e16.alerta_otpp is True


def test_agrupar_etapas_vencimiento_ocs_derivado(db_session):
    """E19 filas include vencimiento_ocs = fecha_inicio + plazo_entrega days."""
    proc = _make_proceso(db_session)
    inicio = date(2026, 6, 1)
    r = EtapaRegistro(
        proceso_id=proc.id,
        codigo_etapa="E19",
        nombre_etapa="OCS",
        area_responsable="OEAS",
        fecha_inicio=inicio,
        plazo_entrega=30,
        nro_ocs="OCS-2026-001",
        monto_ocs=Decimal("100000"),
        estado_etapa="COMPLETADO",
        nro_ronda=1,
        registrado_por="editor1",
    )
    db_session.add(r)
    db_session.flush()

    rows = db_session.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proc.id)
    ).scalars().all()

    grupos = agrupar_etapas(list(rows))
    e19 = next(g for g in grupos if g.cod == "E19")
    assert len(e19.filas) == 1
    expected_venc = inicio + timedelta(days=30)
    assert e19.filas[0].vencimiento_ocs == expected_venc
