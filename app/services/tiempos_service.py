"""Pure service: lead-time / cuello de botella per proceso.

Consumes the same EtapaAgrupadaOut list that agrupar_etapas() already produces.
No DB session, no IO — trivially unit-testable (mirrors calcular_progreso philosophy).

Algorithm:
  1. Walk CADENA (26 non-bucle nodes).
  2. For each cod: compute _fecha_hito() = MAX(fecha_fin ?? fecha_inicio) across filas[].
     Skip if both dates are None, or if the stage is a bucle (es_bucle=True).
  3. Build intervals between consecutive dated milestones.
  4. Aggregate por_area (sum dias by area_responsable of the CLOSING stage).
  5. total_dias = sum of all interval dias.
  6. cuello_de_botella = interval with highest dias (earliest in CADENA wins on tie).

Design decisions (see design artifact §Architecture Decisions):
  - fecha_fin takes precedence over fecha_inicio.
  - Multi-row stages (por_area=True): milestone date = MAX across all filas[].
  - Bucle stages (es_bucle=True or area_responsable="BUCLE") are excluded.
  - Interval attribution: area_responsable of the CLOSING stage.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from app.schemas.etapa import (
    AreaTiempoOut,
    CuelloBotellaOut,
    EtapaAgrupadaOut,
    FilaAreaOut,
    IntervaloOut,
    TiemposProcesoOut,
)
from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO


# ---------------------------------------------------------------------------
# Internal milestone dataclass
# ---------------------------------------------------------------------------

@dataclass
class _Milestone:
    cod: str
    nombre: str
    area_responsable: str
    fecha: date


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fecha_hito(fila_list: list[FilaAreaOut]) -> date | None:
    """Return the milestone date for a stage: MAX(fecha_fin ?? fecha_inicio) across filas.

    Returns None when all rows have neither date set.
    """
    best: date | None = None
    for f in fila_list:
        candidate = f.fecha_fin if f.fecha_fin is not None else f.fecha_inicio
        if candidate is None:
            continue
        if best is None or candidate > best:
            best = candidate
    return best


def _milestones(agrupadas: list[EtapaAgrupadaOut]) -> list[_Milestone]:
    """Walk CADENA and return ordered list of dated milestones.

    Skips:
      - bucle stages (es_bucle=True)
      - stages with area_responsable="BUCLE" (defensive guard)
      - stages with no date (fecha_hito is None)
    """
    by_cod: dict[str, EtapaAgrupadaOut] = {e.cod: e for e in agrupadas}
    result: list[_Milestone] = []

    for cod in CADENA:
        etapa = by_cod.get(cod)
        if etapa is None:
            continue

        # Skip bucles — they are not milestones in the linear chain
        spec = ETAPAS_CATALOGO.get(cod)
        if spec is not None and spec.es_bucle:
            continue
        if etapa.es_bucle or etapa.area_responsable == "BUCLE":
            continue

        fecha = _fecha_hito(etapa.filas)
        if fecha is None:
            continue  # undated stage — gap spans to next dated milestone

        result.append(_Milestone(
            cod=cod,
            nombre=etapa.nombre,
            area_responsable=etapa.area_responsable,
            fecha=fecha,
        ))

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calcular_tiempos(
    etapas_agrupadas: list[EtapaAgrupadaOut],
) -> TiemposProcesoOut:
    """Compute lead-time intelligence from a grouped etapas list.

    Args:
        etapas_agrupadas: Output of etapas_service.agrupar_etapas().

    Returns:
        TiemposProcesoOut with intervalos, total_dias, por_area, cuello_de_botella.
    """
    milestones = _milestones(etapas_agrupadas)

    # Need at least 2 milestones to form any interval
    if len(milestones) < 2:
        return TiemposProcesoOut(
            intervalos=[],
            total_dias=0,
            por_area=[],
            cuello_de_botella=None,
        )

    # Build intervals from consecutive milestones
    intervalos: list[IntervaloOut] = []
    for i in range(1, len(milestones)):
        prev = milestones[i - 1]
        curr = milestones[i]
        dias = (curr.fecha - prev.fecha).days
        intervalos.append(IntervaloOut(
            cod=curr.cod,
            nombre=curr.nombre,
            area_responsable=curr.area_responsable,
            desde=prev.fecha,
            hasta=curr.fecha,
            dias=dias,
        ))

    # Aggregate por_area: sum dias by closing-stage area_responsable
    area_totals: dict[str, int] = defaultdict(int)
    for iv in intervalos:
        area_totals[iv.area_responsable] += iv.dias

    por_area = [
        AreaTiempoOut(area=area, dias_total=total)
        for area, total in area_totals.items()
    ]
    # Sort por_area descending by dias_total for readability (matches frontend expectation)
    por_area.sort(key=lambda x: x.dias_total, reverse=True)

    total_dias = sum(iv.dias for iv in intervalos)

    # cuello_de_botella: max dias (first in list order wins on tie, which is CADENA order)
    cuello: CuelloBotellaOut | None = None
    if intervalos:
        max_iv = max(intervalos, key=lambda iv: iv.dias)
        cuello = CuelloBotellaOut(cod=max_iv.cod, dias=max_iv.dias)

    return TiemposProcesoOut(
        intervalos=intervalos,
        total_dias=total_dias,
        por_area=por_area,
        cuello_de_botella=cuello,
    )
