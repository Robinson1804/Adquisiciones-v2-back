"""Pure service: lead-time and bottleneck calculation per process."""
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
from app.services.etapas_catalogo import ETAPAS_CATALOGO, ORDEN_ETAPAS


@dataclass
class _Milestone:
    cod: str
    nombre: str
    area_responsable: str
    fecha: date


def _fecha_hito(fila_list: list[FilaAreaOut]) -> date | None:
    """Return MAX(fecha_fin ?? fecha_inicio) across the rows of a stage."""
    best: date | None = None
    for fila in fila_list:
        candidate = fila.fecha_fin if fila.fecha_fin is not None else fila.fecha_inicio
        if candidate is None:
            continue
        if best is None or candidate > best:
            best = candidate
    return best


def _milestones(agrupadas: list[EtapaAgrupadaOut]) -> list[_Milestone]:
    """Return dated non-loop milestones in canonical stage order."""
    by_cod: dict[str, EtapaAgrupadaOut] = {etapa.cod: etapa for etapa in agrupadas}
    result: list[_Milestone] = []

    for cod in ORDEN_ETAPAS:
        etapa = by_cod.get(cod)
        if etapa is None:
            continue

        spec = ETAPAS_CATALOGO.get(cod)
        if spec is not None and spec.es_bucle:
            continue
        if etapa.es_bucle or etapa.area_responsable == "BUCLE":
            continue

        fecha = _fecha_hito(etapa.filas)
        if fecha is None:
            continue

        result.append(
            _Milestone(
                cod=cod,
                nombre=etapa.nombre,
                area_responsable=etapa.area_responsable,
                fecha=fecha,
            )
        )

    return result


def calcular_tiempos(etapas_agrupadas: list[EtapaAgrupadaOut]) -> TiemposProcesoOut:
    """Compute interval chain, total days, area totals and bottleneck."""
    milestones = _milestones(etapas_agrupadas)
    if len(milestones) < 2:
        return TiemposProcesoOut(
            intervalos=[],
            total_dias=0,
            por_area=[],
            cuello_de_botella=None,
        )

    intervalos: list[IntervaloOut] = []
    for i in range(1, len(milestones)):
        prev = milestones[i - 1]
        curr = milestones[i]
        dias = (curr.fecha - prev.fecha).days
        intervalos.append(
            IntervaloOut(
                cod=curr.cod,
                nombre=curr.nombre,
                area_responsable=curr.area_responsable,
                desde=prev.fecha,
                hasta=curr.fecha,
                dias=dias,
            )
        )

    area_totals: dict[str, int] = defaultdict(int)
    for intervalo in intervalos:
        area_totals[intervalo.area_responsable] += intervalo.dias

    por_area = [
        AreaTiempoOut(area=area, dias_total=total)
        for area, total in area_totals.items()
    ]
    por_area.sort(key=lambda item: item.dias_total, reverse=True)

    total_dias = sum(intervalo.dias for intervalo in intervalos)
    cuello = None
    if intervalos:
        max_intervalo = max(intervalos, key=lambda item: item.dias)
        cuello = CuelloBotellaOut(cod=max_intervalo.cod, dias=max_intervalo.dias)

    return TiemposProcesoOut(
        intervalos=intervalos,
        total_dias=total_dias,
        por_area=por_area,
        cuello_de_botella=cuello,
    )
