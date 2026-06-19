"""TDD — RED phase tests for tiempos_service.calcular_tiempos.

All 8 test cases from task T02.  Runs against a pure function — no DB needed.
Imported BEFORE the service exists so tests fail with ImportError → RED confirmed.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.schemas.etapa import (
    EtapaAgrupadaOut,
    FilaAreaOut,
    TiemposProcesoOut,
)


# ---------------------------------------------------------------------------
# Helpers to build EtapaAgrupadaOut fixtures without a real DB session
# ---------------------------------------------------------------------------

def _agrupada(
    cod: str,
    nombre: str,
    area_responsable: str,
    filas: list[FilaAreaOut] | None = None,
    es_bucle: bool = False,
    por_area: bool = False,
) -> EtapaAgrupadaOut:
    return EtapaAgrupadaOut(
        cod=cod,
        nombre=nombre,
        area_responsable=area_responsable,
        es_bucle=es_bucle,
        por_area=por_area,
        estado="COMPLETADO",
        filas=filas or [],
        rondas=[],
    )


def _fila(
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    area_usuaria: str | None = None,
) -> FilaAreaOut:
    return FilaAreaOut(
        id=1,
        area_usuaria=area_usuaria,
        estado_etapa="COMPLETADO",
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        dias=None,
        cmn_adjunto=None,
        monto_cert=None,
        resultado_eval=None,
        nro_ocs=None,
        monto_ocs=None,
        plazo_entrega=None,
        fecha_envio_otpp=None,
        fecha_resp_otpp=None,
        responsable=None,
        oficio_correo=None,
        observaciones=None,
        registrado_por=None,
    )


# ---------------------------------------------------------------------------
# Shared agrupadas for the "demo chain" test case
# E01a(2026-03-04) → E02b(2026-03-24) → E03(2026-04-07) → E19(2026-04-17) → E23(2026-05-11)
# Expected intervals: 20, 14, 10, 24 días; total=68; cuello=E23(24)
# ---------------------------------------------------------------------------

def _build_demo_agrupadas() -> list[EtapaAgrupadaOut]:
    """Build a minimal agrupar_etapas-shaped list for the demo chain.

    Only the five stages in the chain are dated; others have empty filas (no date).
    We build the full CADENA-order list so calcular_tiempos can walk it correctly.
    """
    from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

    dated = {
        "E01a": date(2026, 3, 4),
        "E02b": date(2026, 3, 24),
        "E03":  date(2026, 4, 7),
        "E19":  date(2026, 4, 17),
        "E23":  date(2026, 5, 11),
    }
    result = []
    for cod in CADENA:
        spec = ETAPAS_CATALOGO[cod]
        d = dated.get(cod)
        filas = [_fila(fecha_fin=d)] if d else []
        result.append(_agrupada(
            cod=cod,
            nombre=spec.nombre,
            area_responsable=spec.area_responsable,
            filas=filas,
        ))
    return result


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestCalcularTiempos:
    """Table-driven tests for calcular_tiempos (8 cases)."""

    def test_case1_demo_chain(self):
        """Case 1: demo chain → dias=[20,14,10,24], total=68, cuello=E23."""
        from app.services.tiempos_service import calcular_tiempos

        agrupadas = _build_demo_agrupadas()
        result = calcular_tiempos(agrupadas)

        assert isinstance(result, TiemposProcesoOut)
        assert len(result.intervalos) == 4
        dias = [i.dias for i in result.intervalos]
        assert dias == [20, 14, 10, 24], f"Expected [20,14,10,24], got {dias}"
        assert result.total_dias == 68
        assert result.cuello_de_botella is not None
        assert result.cuello_de_botella.cod == "E23"
        assert result.cuello_de_botella.dias == 24

    def test_case2_empty(self):
        """Case 2: no dated milestones → empty result."""
        from app.services.tiempos_service import calcular_tiempos
        from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

        agrupadas = [
            _agrupada(cod=cod, nombre=ETAPAS_CATALOGO[cod].nombre,
                      area_responsable=ETAPAS_CATALOGO[cod].area_responsable)
            for cod in CADENA
        ]
        result = calcular_tiempos(agrupadas)

        assert result.intervalos == []
        assert result.total_dias == 0
        assert result.por_area == []
        assert result.cuello_de_botella is None

    def test_case3_single_milestone(self):
        """Case 3: only E01a dated → empty intervals (need 2 milestones)."""
        from app.services.tiempos_service import calcular_tiempos
        from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

        agrupadas = []
        for cod in CADENA:
            spec = ETAPAS_CATALOGO[cod]
            filas = [_fila(fecha_fin=date(2026, 1, 1))] if cod == "E01a" else []
            agrupadas.append(_agrupada(
                cod=cod, nombre=spec.nombre,
                area_responsable=spec.area_responsable, filas=filas
            ))
        result = calcular_tiempos(agrupadas)

        assert result.intervalos == []
        assert result.total_dias == 0
        assert result.cuello_de_botella is None

    def test_case4_gap_skip(self):
        """Case 4: E01a dated, E01b undated, E02b dated → single interval spanning gap."""
        from app.services.tiempos_service import calcular_tiempos
        from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

        dated = {
            "E01a": date(2026, 1, 1),
            "E02b": date(2026, 1, 15),
        }
        agrupadas = []
        for cod in CADENA:
            spec = ETAPAS_CATALOGO[cod]
            d = dated.get(cod)
            filas = [_fila(fecha_fin=d)] if d else []
            agrupadas.append(_agrupada(
                cod=cod, nombre=spec.nombre,
                area_responsable=spec.area_responsable, filas=filas
            ))
        result = calcular_tiempos(agrupadas)

        assert len(result.intervalos) == 1
        assert result.intervalos[0].cod == "E02b"
        assert result.intervalos[0].dias == 14

    def test_case5_multi_row_max_collapse(self):
        """Case 5: multi-area stage (E01c) → milestone = MAX fecha_fin across rows."""
        from app.services.tiempos_service import calcular_tiempos
        from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

        # E01a dated 2025-01-01, E01c has 3 rows with fecha_fin 05/08/03
        # → milestone for E01c = 2025-01-08; then E02 dated 2025-01-20
        dated_simple = {
            "E01a": date(2025, 1, 1),
            "E02":  date(2025, 1, 20),
        }
        agrupadas = []
        for cod in CADENA:
            spec = ETAPAS_CATALOGO[cod]
            if cod == "E01c":
                filas = [
                    _fila(fecha_fin=date(2025, 1, 5)),
                    _fila(fecha_fin=date(2025, 1, 8)),
                    _fila(fecha_fin=date(2025, 1, 3)),
                ]
                agrupadas.append(_agrupada(
                    cod=cod, nombre=spec.nombre,
                    area_responsable=spec.area_responsable,
                    filas=filas, por_area=True
                ))
            else:
                d = dated_simple.get(cod)
                filas = [_fila(fecha_fin=d)] if d else []
                agrupadas.append(_agrupada(
                    cod=cod, nombre=spec.nombre,
                    area_responsable=spec.area_responsable, filas=filas
                ))

        result = calcular_tiempos(agrupadas)

        # Intervals: E01a→E01c (7 days, since 2025-01-01 to 2025-01-08)
        #            E01c→E02  (12 days, since 2025-01-08 to 2025-01-20)
        assert len(result.intervalos) >= 2
        cods = [i.cod for i in result.intervalos]
        assert "E01c" in cods
        e01c_interval = next(i for i in result.intervalos if i.cod == "E01c")
        assert e01c_interval.dias == 7  # 2025-01-08 - 2025-01-01

    def test_case6_fecha_fin_over_fecha_inicio(self):
        """Case 6: stage has both fecha_inicio and fecha_fin → uses fecha_fin."""
        from app.services.tiempos_service import calcular_tiempos
        from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

        # E01a: fecha_fin=2026-03-04 (should be used, not fecha_inicio=2026-03-01)
        # E02: fecha_fin=2026-03-14
        # → interval E01a→E02 = 10 days (using fecha_fin dates)
        dated: dict[str, tuple[date | None, date | None]] = {
            "E01a": (date(2026, 3, 1), date(2026, 3, 4)),   # (inicio, fin)
            "E02":  (date(2026, 3, 10), date(2026, 3, 14)),
        }
        agrupadas = []
        for cod in CADENA:
            spec = ETAPAS_CATALOGO[cod]
            if cod in dated:
                inicio, fin = dated[cod]
                filas = [_fila(fecha_inicio=inicio, fecha_fin=fin)]
            else:
                filas = []
            agrupadas.append(_agrupada(
                cod=cod, nombre=spec.nombre,
                area_responsable=spec.area_responsable, filas=filas
            ))

        result = calcular_tiempos(agrupadas)

        assert len(result.intervalos) == 1
        assert result.intervalos[0].dias == 10  # 2026-03-14 - 2026-03-04

    def test_case7_tiebreak_cuello(self):
        """Case 7: two equal-max intervals → first in CADENA order wins."""
        from app.services.tiempos_service import calcular_tiempos
        from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

        # E01a → E02 (10 days) → E03 (10 days) → tie; E02 is first → cuello=E02
        dated = {
            "E01a": date(2026, 1, 1),
            "E02":  date(2026, 1, 11),
            "E03":  date(2026, 1, 21),
        }
        agrupadas = []
        for cod in CADENA:
            spec = ETAPAS_CATALOGO[cod]
            d = dated.get(cod)
            filas = [_fila(fecha_fin=d)] if d else []
            agrupadas.append(_agrupada(
                cod=cod, nombre=spec.nombre,
                area_responsable=spec.area_responsable, filas=filas
            ))

        result = calcular_tiempos(agrupadas)

        assert len(result.intervalos) == 2
        assert result.intervalos[0].dias == 10
        assert result.intervalos[1].dias == 10
        assert result.cuello_de_botella is not None
        assert result.cuello_de_botella.cod == "E02"  # first in CADENA wins on tie

    def test_case8_por_area_aggregation(self):
        """Case 8: por_area totals are summed correctly across intervals."""
        from app.services.tiempos_service import calcular_tiempos
        from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

        # E01a(AREAS, 2026-01-01) → E02b(AREAS, 2026-01-11, 10 days)
        # → E03(OTIN, 2026-01-21, 10 days)
        # por_area: AREAS=10 (E02b), OTIN=10 (E03)
        dated = {
            "E01a": date(2026, 1, 1),
            "E02b": date(2026, 1, 11),
            "E03":  date(2026, 1, 21),
        }
        agrupadas = []
        for cod in CADENA:
            spec = ETAPAS_CATALOGO[cod]
            d = dated.get(cod)
            filas = [_fila(fecha_fin=d)] if d else []
            agrupadas.append(_agrupada(
                cod=cod, nombre=spec.nombre,
                area_responsable=spec.area_responsable, filas=filas
            ))

        result = calcular_tiempos(agrupadas)

        area_totals = {pa.area: pa.dias_total for pa in result.por_area}
        # E02b area_responsable=AREAS, E03 area_responsable=OTIN
        assert area_totals.get("AREAS") == 10
        assert area_totals.get("OTIN") == 10
        assert result.total_dias == 20
