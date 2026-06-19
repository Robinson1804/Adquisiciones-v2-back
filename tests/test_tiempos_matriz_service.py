"""TDD — tests for tiempos_matriz.construir_matriz (service unit tests).

Pure function — no DB session needed.
All fixtures use frozen dates and in-memory stubs (SimpleNamespace, not ORM models).

DB isolation: this file has NO DB access.  The conftest autouse fixture
_clean_business_tables only runs DELETEs inside the _test_engine which
points to dashboard_test — adquisiciones_tic is never touched.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.tiempos_matriz import COLUMNAS_MATRIZ, construir_matriz
from app.schemas.tiempos_matriz import MatrizTiemposOut


# ---------------------------------------------------------------------------
# Stub builders (SimpleNamespace — bypass SQLAlchemy ORM entirely)
# construir_matriz only accesses .id, .id_proceso, .requerimiento on Proceso
# and .proceso_id, .codigo_etapa, .nombre_etapa, .area_responsable,
# .fecha_inicio, .fecha_fin, .estado_etapa, .es_bucle, .area_usuaria,
# .nro_ronda, .monto_cert, .alerta_otpp on EtapaRegistro (via agrupar_etapas).
# ---------------------------------------------------------------------------

def _proceso(
    pid: int,
    id_proceso: str = "2026-001",
    pim=None,
    estado: str = "EN PROCESO",
    tipo: str | None = "SERVICIO",
) -> SimpleNamespace:
    """Minimal Proceso-like stub (plain object, not ORM)."""
    from decimal import Decimal
    return SimpleNamespace(
        id=pid,
        id_proceso=id_proceso,
        requerimiento=f"Requerimiento {id_proceso}",
        eliminado_en=None,
        pim=Decimal(str(pim)) if pim is not None else None,
        estado=estado,
        tipo=tipo,
    )


def _etapa_row(
    proceso_id: int,
    cod: str,
    fecha_fin: date | None = None,
    fecha_inicio: date | None = None,
) -> SimpleNamespace:
    """Minimal EtapaRegistro-like stub with frozen dates (no DB required)."""
    from app.services.etapas_catalogo import ETAPAS_CATALOGO
    spec = ETAPAS_CATALOGO.get(cod)
    dias: int | None = None
    if fecha_fin is not None and fecha_inicio is not None:
        dias = (fecha_fin - fecha_inicio).days
    return SimpleNamespace(
        id=1,
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=spec.nombre if spec else cod,
        area_responsable=spec.area_responsable if spec else "OTIN",
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        estado_etapa="COMPLETADO",
        es_bucle=False,
        area_usuaria=None,
        nro_ronda=1,
        motivo_bucle=None,
        monto_cert=None,
        resultado_eval=None,
        cmn_adjunto=None,
        nro_ocs=None,
        monto_ocs=None,
        plazo_entrega=None,
        fecha_envio_otpp=None,
        fecha_resp_otpp=None,
        fecha_limite_respuesta=None,
        cmn_siga_confirmado=None,
        titulo_ronda=None,
        responsable=None,
        oficio_correo=None,
        observaciones=None,
        registrado_por=None,
        dias=dias,
    )


# ---------------------------------------------------------------------------
# Number of hito columns (excluding TOTAL)
# ---------------------------------------------------------------------------
N_HITO_COLS = len(COLUMNAS_MATRIZ)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestConstruirMatriz:

    def test_shape_with_single_proceso(self):
        """Single proceso → 1 fila, columnas matches catalogue, promedios aligned."""
        procesos = [_proceso(1, "2026-001")]
        # E01a(2026-01-01) → E01c(2026-01-11) → E02(2026-01-21) gives two intervals
        # closing at E01c (cod E01c) and E02 (cod E02)
        etapas = [
            _etapa_row(1, "E01a", fecha_fin=date(2026, 1, 1)),
            _etapa_row(1, "E01c", fecha_fin=date(2026, 1, 11)),
            _etapa_row(1, "E02",  fecha_fin=date(2026, 1, 21)),
        ]
        result = construir_matriz(procesos, etapas)

        assert isinstance(result, MatrizTiemposOut)
        assert len(result.filas) == 1
        # columnas = N_HITO_COLS hito columns + 1 TOTAL = N_HITO_COLS + 1
        assert len(result.columnas) == N_HITO_COLS + 1
        assert result.columnas[-1].key == "total"
        assert result.columnas[-1].cod is None
        # promedios aligned to columnas (including TOTAL)
        assert len(result.promedios) == N_HITO_COLS + 1
        assert len(result.filas[0].celdas) == N_HITO_COLS

    def test_cell_value_equals_interval_dias_by_cod(self):
        """Cell value == interval.dias for the matching cod column."""
        # E01a(2026-01-01) → E01c(2026-01-11) → 10 days closing at E01c (CMN col)
        # E01c(2026-01-11) → E02(2026-01-21) → 10 days closing at E02 (TDR col)
        procesos = [_proceso(1, "2026-001")]
        etapas = [
            _etapa_row(1, "E01a", fecha_fin=date(2026, 1, 1)),
            _etapa_row(1, "E01c", fecha_fin=date(2026, 1, 11)),
            _etapa_row(1, "E02",  fecha_fin=date(2026, 1, 21)),
        ]
        result = construir_matriz(procesos, etapas)

        fila = result.filas[0]
        # columnas list: cmn=0, tdr=1, vb=2, indagacion=3, cuadro_comp=4, os=5, conformidad=6
        cmn_idx = next(i for i, c in enumerate(result.columnas) if c.key == "cmn")
        tdr_idx = next(i for i, c in enumerate(result.columnas) if c.key == "tdr")
        # cmn closes at E01c: 10 days (2026-01-11 - 2026-01-01)
        assert fila.celdas[cmn_idx] == 10
        # tdr closes at E02: 10 days (2026-01-21 - 2026-01-11)
        assert fila.celdas[tdr_idx] == 10

    def test_missing_cod_gives_none(self):
        """Column cod not present in proceso's intervals → cell is None."""
        procesos = [_proceso(1, "2026-001")]
        # Only E01a and E01c → only CMN interval; all other hito cods → None
        etapas = [
            _etapa_row(1, "E01a", fecha_fin=date(2026, 1, 1)),
            _etapa_row(1, "E01c", fecha_fin=date(2026, 1, 15)),
        ]
        result = construir_matriz(procesos, etapas)
        fila = result.filas[0]
        # tdr (E02), vb (E02b), etc. should all be None
        for i, col in enumerate(result.columnas):
            if col.key == "cmn":
                assert fila.celdas[i] == 14  # 2026-01-15 - 2026-01-01 = 14
            elif col.key != "total":
                assert fila.celdas[i] is None, f"Expected None for col {col.key}"

    def test_total_from_total_dias_not_sum_of_visible_cols(self):
        """TOTAL = total_dias from calcular_tiempos, not sum of hito columns."""
        # E01a → E04 (a CADENA stage NOT in our 7 hito columns) → E09
        # The interval E04→E09 covers dias that aren't in any hito column.
        # So total_dias > sum(non-None celdas).
        procesos = [_proceso(1, "2026-001")]
        etapas = [
            _etapa_row(1, "E01a", fecha_fin=date(2026, 1, 1)),
            _etapa_row(1, "E04",  fecha_fin=date(2026, 1, 16)),   # not a hito col
            _etapa_row(1, "E09",  fecha_fin=date(2026, 2, 5)),    # cuadro_comp
        ]
        result = construir_matriz(procesos, etapas)
        fila = result.filas[0]
        # total_dias = (E04-E01a) + (E09-E04) = 15 + 20 = 35
        assert fila.total_dias == 35

        cuadro_idx = next(i for i, c in enumerate(result.columnas) if c.key == "cuadro_comp")
        # The E09 interval closes at E09 with cod E09; dias = 2026-02-05 - 2026-01-16 = 20
        assert fila.celdas[cuadro_idx] == 20

        # Sum of non-null celdas = 20 (only cuadro_comp); total_dias = 35 (includes E04 interval)
        non_null_sum = sum(v for v in fila.celdas if v is not None)
        assert non_null_sum < fila.total_dias

    def test_promedios_ignores_none(self):
        """Per-column average uses only non-null cells; processes with null don't dilute."""
        # Proceso A: E01a→E01c = 10 days (cmn=10)
        # Proceso B: no E01c (cmn=None)
        # Average for cmn = 10 / 1 = 10.0 (not 5.0)
        p_a = _proceso(1, "2026-001")
        p_b = _proceso(2, "2026-002")
        etapas = [
            _etapa_row(1, "E01a", fecha_fin=date(2026, 1, 1)),
            _etapa_row(1, "E01c", fecha_fin=date(2026, 1, 11)),
            _etapa_row(2, "E01a", fecha_fin=date(2026, 2, 1)),
            # proceso 2 has no E01c
        ]
        result = construir_matriz([p_a, p_b], etapas)
        assert len(result.filas) == 2
        cmn_idx = next(i for i, c in enumerate(result.columnas) if c.key == "cmn")
        # Only 1 non-null value (10) → average = 10.0
        assert result.promedios[cmn_idx] == pytest.approx(10.0)

    def test_empty_procesos_list(self):
        """Empty procesos list → filas=[], promedios all None, promedio_total None."""
        result = construir_matriz([], [])
        assert isinstance(result, MatrizTiemposOut)
        assert result.filas == []
        assert all(p is None for p in result.promedios)
        assert result.promedio_total is None

    def test_fila_includes_pim_estado_tipo(self):
        """FilaMatrizOut must expose pim, estado, and tipo from the Proceso stub."""
        from decimal import Decimal
        proceso = _proceso(1, "2026-001", pim=5000, estado="CULMINADO", tipo="BIEN")
        result = construir_matriz([proceso], [])
        fila = result.filas[0]
        assert fila.pim == Decimal("5000")
        assert fila.estado == "CULMINADO"
        assert fila.tipo == "BIEN"

    def test_fila_pim_none_when_proceso_pim_is_none(self):
        """pim=None on Proceso → fila.pim is None."""
        proceso = _proceso(1, "2026-001", pim=None)
        result = construir_matriz([proceso], [])
        assert result.filas[0].pim is None

    def test_three_procesos_matrix_shape(self):
        """3 procesos → 3 filas; promedios average correctly across all."""
        # Proceso A: cmn=10
        # Proceso B: cmn=20
        # Proceso C: cmn=None, os=30
        p_a = _proceso(1, "2026-001")
        p_b = _proceso(2, "2026-002")
        p_c = _proceso(3, "2026-003")
        etapas = [
            # A: E01a→E01c = 10
            _etapa_row(1, "E01a", fecha_fin=date(2026, 1, 1)),
            _etapa_row(1, "E01c", fecha_fin=date(2026, 1, 11)),
            # B: E01a→E01c = 20
            _etapa_row(2, "E01a", fecha_fin=date(2026, 2, 1)),
            _etapa_row(2, "E01c", fecha_fin=date(2026, 2, 21)),
            # C: E01a→E19 (os), skipping middle cods
            _etapa_row(3, "E01a", fecha_fin=date(2026, 3, 1)),
            _etapa_row(3, "E19",  fecha_fin=date(2026, 3, 31)),
        ]
        result = construir_matriz([p_a, p_b, p_c], etapas)
        assert len(result.filas) == 3
        cmn_idx = next(i for i, c in enumerate(result.columnas) if c.key == "cmn")
        os_idx = next(i for i, c in enumerate(result.columnas) if c.key == "os")
        # cmn average: (10 + 20) / 2 = 15.0 (C has None cmn)
        assert result.promedios[cmn_idx] == pytest.approx(15.0)
        # os average: 30.0 / 1 (only C has a value — 2026-03-31 - 2026-03-01 = 30)
        assert result.promedios[os_idx] == pytest.approx(30.0)
