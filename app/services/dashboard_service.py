"""Dashboard aggregation service — C4 executive dashboard (read-only).

5 pure IO functions (receive Session + anno). Each always applies:
  - Proceso.eliminado_en.is_(None)   (INV-3: exclude soft-deleted)
  - Proceso.anno == anno              (INV-2: filter by year)

No writes. No mutations. Design authority: design #152.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.etapa import EtapaRegistro
from app.models.montos import MontosProceso
from app.models.proceso import Proceso
from app.schemas.dashboard import (
    DemoraAreaOut,
    DemoraAreasResponse,
    FaseProgresoOut,
    FlujoProcesosResponse,
    MetricasOut,
    PresupuestoProcesoOut,
    PresupuestoResponse,
    ProcesoFlujoOut,
    TiempoEtapaOut,
    TiemposEtapaResponse,
)
from app.services.etapas_catalogo import (
    COD_A_FASE,
    ETAPAS_CATALOGO,
    FASES,
    ORDEN_ETAPAS,
    fase_de_cod,
)
from app.services.etapas_service import calcular_progreso

# ---------------------------------------------------------------------------
# Semáforo thresholds (D4 — calibrated to institutional volume, recalibrable)
# ---------------------------------------------------------------------------

SEMAFORO_VERDE_MAX = 7       # days — within expected range
SEMAFORO_AMARILLO_MAX = 15   # days — attention needed

# Loop codes excluded from the tiempos-etapa main set
_BUCLE_CODS: frozenset[str] = frozenset(
    cod for cod, spec in ETAPAS_CATALOGO.items() if spec.es_bucle
)

# Ordered list of non-bucle stage codes (for tiempos-etapa response ordering)
_MAIN_CODS_ORDERED: list[str] = [
    cod for cod in ORDEN_ETAPAS if cod not in _BUCLE_CODS
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _semaforo(dias: float | None) -> str | None:
    """Return semáforo string based on average days (D4 thresholds)."""
    if dias is None:
        return None
    if dias <= SEMAFORO_VERDE_MAX:
        return "verde"
    if dias <= SEMAFORO_AMARILLO_MAX:
        return "amarillo"
    return "rojo"


def _variacion(nuevo, base) -> float | None:
    """Compute percentage variation (nuevo - base) / base * 100, null-safe."""
    if nuevo is None or base is None or float(base) == 0:
        return None
    return round((float(nuevo) - float(base)) / float(base) * 100, 1)


# ---------------------------------------------------------------------------
# T3.1 — get_metricas
# ---------------------------------------------------------------------------

def get_metricas(db: Session, anno: int) -> MetricasOut:
    """Return 6 KPI counts for the given year.

    dias_promedio: calendar span E01.fecha_inicio → E25.fecha_fin for CULMINADOS.
    pim_total: SUM(procesos.pim), treating NULL as 0.
    Empty year → zeros + dias_promedio=None (INV-4).
    """
    base_q = select(Proceso).where(
        Proceso.anno == anno,
        Proceso.eliminado_en.is_(None),
    )
    procesos = db.execute(base_q).scalars().all()

    total = len(procesos)
    en_proceso = sum(1 for p in procesos if p.estado == "EN PROCESO")
    culminados = sum(1 for p in procesos if p.estado == "CULMINADO")
    cancelados = sum(1 for p in procesos if p.estado == "CANCELADO")
    pim_total: float | None = None

    if total == 0:
        return MetricasOut(
            anno=anno,
            total=0,
            en_proceso=0,
            culminados=0,
            cancelados=0,
            pim_total=0.0,
            dias_promedio=None,
        )

    pim_sum = sum(float(p.pim) for p in procesos if p.pim is not None)
    pim_total = pim_sum

    dias_promedio = _calcular_dias_promedio_culminados(db, anno)

    return MetricasOut(
        anno=anno,
        total=total,
        en_proceso=en_proceso,
        culminados=culminados,
        cancelados=cancelados,
        pim_total=pim_total,
        dias_promedio=dias_promedio,
    )


def _calcular_dias_promedio_culminados(db: Session, anno: int) -> float | None:
    """AVG(E25.fecha_fin - E01_min.fecha_inicio) for CULMINADO processes.

    Design D2: calendar span is the executive metric (not SUM of etapa.dias).
    Returns None if zero CULMINADO processes have both E01 and E25 dates.
    """
    culminado_ids = db.execute(
        select(Proceso.id).where(
            Proceso.anno == anno,
            Proceso.eliminado_en.is_(None),
            Proceso.estado == "CULMINADO",
        )
    ).scalars().all()

    if not culminado_ids:
        return None

    # E01 min fecha_inicio per proceso
    e01_rows = db.execute(
        select(
            EtapaRegistro.proceso_id,
            func.min(EtapaRegistro.fecha_inicio).label("fecha_inicio_total"),
        ).where(
            EtapaRegistro.proceso_id.in_(culminado_ids),
            EtapaRegistro.codigo_etapa == "E01",
            EtapaRegistro.fecha_inicio.is_not(None),
        ).group_by(EtapaRegistro.proceso_id)
    ).all()
    e01_by_proc = {row.proceso_id: row.fecha_inicio_total for row in e01_rows}

    # E25 fecha_fin per proceso
    e25_rows = db.execute(
        select(
            EtapaRegistro.proceso_id,
            EtapaRegistro.fecha_fin,
        ).where(
            EtapaRegistro.proceso_id.in_(culminado_ids),
            EtapaRegistro.codigo_etapa == "E25",
            EtapaRegistro.estado_etapa == "COMPLETADO",
            EtapaRegistro.fecha_fin.is_not(None),
        )
    ).all()
    e25_by_proc = {row.proceso_id: row.fecha_fin for row in e25_rows}

    spans = []
    for pid in culminado_ids:
        inicio = e01_by_proc.get(pid)
        fin = e25_by_proc.get(pid)
        if inicio and fin:
            spans.append((fin - inicio).days)

    if not spans:
        return None
    return round(sum(spans) / len(spans), 1)


# ---------------------------------------------------------------------------
# T3.2 — get_flujo_procesos
# ---------------------------------------------------------------------------

def get_flujo_procesos(db: Session, anno: int) -> FlujoProcesosResponse:
    """Return each proceso with its 5-phase mini-timeline state.

    Reuses calcular_progreso from etapas_service.
    fase_actual derived via fase_de_cod(etapa_actual).
    CULMINADO → all 5 fases completada=True.
    CANCELADO → fase where it stopped, no subsequent phases completada.
    """
    procesos = db.execute(
        select(Proceso).where(
            Proceso.anno == anno,
            Proceso.eliminado_en.is_(None),
        ).order_by(Proceso.id)
    ).scalars().all()

    if not procesos:
        return FlujoProcesosResponse(anno=anno, procesos=[])

    proceso_ids = [p.id for p in procesos]
    etapas_rows = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id.in_(proceso_ids)
        )
    ).scalars().all()

    etapas_by_proc: dict[int, list[EtapaRegistro]] = {}
    for row in etapas_rows:
        etapas_by_proc.setdefault(row.proceso_id, []).append(row)

    result = []
    for p in procesos:
        rows = etapas_by_proc.get(p.id, [])
        progreso = calcular_progreso(rows)

        if p.estado == "CULMINADO":
            # CULMINADO override: process is fully done; no "current" phase.
            # porcentaje=100, fase_actual=None, all fases completada=True.
            fase_actual = None
            porcentaje = 100.0
        else:
            if progreso.etapa_actual and progreso.etapa_actual in COD_A_FASE:
                fase_actual = fase_de_cod(progreso.etapa_actual)
            else:
                fase_actual = "F1"
            porcentaje = progreso.porcentaje

        fases = _build_fases_progreso(fase_actual or "F5", p.estado)

        result.append(ProcesoFlujoOut(
            id=p.id,
            id_proceso=p.id_proceso,
            requerimiento=p.requerimiento,
            estado=p.estado,
            fase_actual=fase_actual,
            porcentaje=porcentaje,
            fases=fases,
        ))

    return FlujoProcesosResponse(anno=anno, procesos=result)


def _build_fases_progreso(
    fase_actual: str,
    estado_proceso: str,
) -> list[FaseProgresoOut]:
    """Build the 5 FaseProgresoOut entries based on current phase and state."""
    orden_actual = FASES[fase_actual]["orden"]
    fases_out = []
    for fkey, fdata in sorted(FASES.items(), key=lambda x: x[1]["orden"]):
        orden = fdata["orden"]
        if estado_proceso == "CULMINADO":
            completada = True
            actual = False
        else:
            completada = orden < orden_actual
            actual = orden == orden_actual
        fases_out.append(FaseProgresoOut(
            fase=fkey,
            label=fdata["label"],
            completada=completada,
            actual=actual,
        ))
    return fases_out


# ---------------------------------------------------------------------------
# T3.3 — get_tiempos_etapa
# ---------------------------------------------------------------------------

def get_tiempos_etapa(db: Session, anno: int) -> TiemposEtapaResponse:
    """Return AVG(dias) per non-bucle stage code for the given year.

    Excludes: OMITIDO, dias IS NULL, bucle codes (E05/E06/E08a/E08b).
    Ordered by ORDEN_ETAPAS (catalog order, E01→E25).
    promedio_global = AVG of the non-null dias_promedio values.
    """
    proceso_ids = db.execute(
        select(Proceso.id).where(
            Proceso.anno == anno,
            Proceso.eliminado_en.is_(None),
        )
    ).scalars().all()

    if not proceso_ids:
        return TiemposEtapaResponse(anno=anno, promedio_global=None, etapas=[])

    rows = db.execute(
        select(
            EtapaRegistro.codigo_etapa,
            func.avg(EtapaRegistro.dias).label("avg_dias"),
            func.count(EtapaRegistro.id).label("n"),
        ).where(
            EtapaRegistro.proceso_id.in_(proceso_ids),
            EtapaRegistro.dias.is_not(None),
            EtapaRegistro.estado_etapa != "OMITIDO",
            EtapaRegistro.codigo_etapa.not_in(list(_BUCLE_CODS)),
        ).group_by(EtapaRegistro.codigo_etapa)
    ).all()

    avg_by_cod: dict[str, tuple[float, int]] = {
        row.codigo_etapa: (float(row.avg_dias), int(row.n))
        for row in rows
    }

    etapas_out = []
    for cod in _MAIN_CODS_ORDERED:
        spec = ETAPAS_CATALOGO[cod]
        if cod in avg_by_cod:
            avg_dias, n = avg_by_cod[cod]
            etapas_out.append(TiempoEtapaOut(
                codigo=cod,
                nombre=spec.nombre,
                area_responsable=spec.area_responsable,
                dias_promedio=round(avg_dias, 1),
                n=n,
            ))
        # Etapas with no data are omitted (only include those with data per design)

    if not etapas_out:
        return TiemposEtapaResponse(anno=anno, promedio_global=None, etapas=[])

    non_null = [e.dias_promedio for e in etapas_out if e.dias_promedio is not None]
    promedio_global = round(sum(non_null) / len(non_null), 1) if non_null else None

    return TiemposEtapaResponse(
        anno=anno,
        promedio_global=promedio_global,
        etapas=etapas_out,
    )


# ---------------------------------------------------------------------------
# T3.4 — get_presupuesto
# ---------------------------------------------------------------------------

def get_presupuesto(db: Session, anno: int) -> PresupuestoResponse:
    """Return per-proceso budget data with LEFT JOIN to montos_proceso.

    All non-deleted procesos for the year are included, even without montos row.
    Variations computed server-side via _variacion() (null-safe).
    Totales = SUM of each monetary field (NULL treated as 0).
    """
    rows = db.execute(
        select(Proceso, MontosProceso).outerjoin(
            MontosProceso,
            Proceso.id == MontosProceso.proceso_id,
        ).where(
            Proceso.anno == anno,
            Proceso.eliminado_en.is_(None),
        ).order_by(Proceso.id)
    ).all()

    if not rows:
        return PresupuestoResponse(
            anno=anno,
            totales={"pim": 0.0, "valor_em": 0.0, "monto_cert_total": 0.0, "monto_ocs": 0.0},
            procesos=[],
        )

    procesos_out = []
    sum_pim = 0.0
    sum_em = 0.0
    sum_cert = 0.0
    sum_ocs = 0.0

    for proceso, montos in rows:
        pim = float(proceso.pim) if proceso.pim is not None else None
        valor_em = float(montos.valor_em) if montos and montos.valor_em is not None else None
        monto_cert = float(montos.monto_cert_total) if montos and montos.monto_cert_total is not None else None
        monto_ocs = float(montos.monto_ocs) if montos and montos.monto_ocs is not None else None

        sum_pim += float(proceso.pim) if proceso.pim is not None else 0.0
        sum_em += float(montos.valor_em) if montos and montos.valor_em is not None else 0.0
        sum_cert += float(montos.monto_cert_total) if montos and montos.monto_cert_total is not None else 0.0
        sum_ocs += float(montos.monto_ocs) if montos and montos.monto_ocs is not None else 0.0

        procesos_out.append(PresupuestoProcesoOut(
            id=proceso.id,
            id_proceso=proceso.id_proceso,
            requerimiento=proceso.requerimiento,
            estado=proceso.estado,
            pim=pim,
            valor_em=valor_em,
            monto_cert_total=monto_cert,
            monto_ocs=monto_ocs,
            var_em_vs_pim=_variacion(valor_em, pim),
            var_cert_vs_em=_variacion(monto_cert, valor_em),
            var_ocs_vs_em=_variacion(monto_ocs, valor_em),
        ))

    return PresupuestoResponse(
        anno=anno,
        totales={
            "pim": sum_pim,
            "valor_em": sum_em,
            "monto_cert_total": sum_cert,
            "monto_ocs": sum_ocs,
        },
        procesos=procesos_out,
    )


# ---------------------------------------------------------------------------
# T3.5 — get_demora_areas
# ---------------------------------------------------------------------------

def get_demora_areas(db: Session, anno: int) -> DemoraAreasResponse:
    """Return AVG days per area for E11 (cert. presupuestal) and E24 (conformidad).

    Only areas with at least one E11 or E24 COMPLETADO row are included.
    Semáforo applied per D4 thresholds (verde<=7, amarillo<=15, rojo>15).
    """
    proceso_ids = db.execute(
        select(Proceso.id).where(
            Proceso.anno == anno,
            Proceso.eliminado_en.is_(None),
        )
    ).scalars().all()

    if not proceso_ids:
        return DemoraAreasResponse(anno=anno, areas=[])

    # E11 aggregation
    e11_rows = db.execute(
        select(
            EtapaRegistro.area_usuaria,
            func.avg(EtapaRegistro.dias).label("avg_dias"),
            func.count(EtapaRegistro.id).label("n"),
        ).where(
            EtapaRegistro.proceso_id.in_(proceso_ids),
            EtapaRegistro.codigo_etapa == "E11",
            EtapaRegistro.estado_etapa == "COMPLETADO",
            EtapaRegistro.dias.is_not(None),
            EtapaRegistro.area_usuaria.is_not(None),
        ).group_by(EtapaRegistro.area_usuaria)
    ).all()

    # E24 aggregation
    e24_rows = db.execute(
        select(
            EtapaRegistro.area_usuaria,
            func.avg(EtapaRegistro.dias).label("avg_dias"),
            func.count(EtapaRegistro.id).label("n"),
        ).where(
            EtapaRegistro.proceso_id.in_(proceso_ids),
            EtapaRegistro.codigo_etapa == "E24",
            EtapaRegistro.estado_etapa == "COMPLETADO",
            EtapaRegistro.dias.is_not(None),
            EtapaRegistro.area_usuaria.is_not(None),
        ).group_by(EtapaRegistro.area_usuaria)
    ).all()

    # Merge by area_usuaria
    area_data: dict[str, dict] = {}

    for row in e11_rows:
        area = row.area_usuaria
        area_data.setdefault(area, {})
        area_data[area]["e11_avg"] = float(row.avg_dias)
        area_data[area]["e11_n"] = int(row.n)

    for row in e24_rows:
        area = row.area_usuaria
        area_data.setdefault(area, {})
        area_data[area]["e24_avg"] = float(row.avg_dias)
        area_data[area]["e24_n"] = int(row.n)

    areas_out = []
    for area, data in sorted(area_data.items()):
        e11_avg = data.get("e11_avg")
        e11_n = data.get("e11_n", 0)
        e24_avg = data.get("e24_avg")
        e24_n = data.get("e24_n", 0)
        areas_out.append(DemoraAreaOut(
            area_usuaria=area,
            e11_dias_promedio=round(e11_avg, 1) if e11_avg is not None else None,
            e11_n=e11_n,
            semaforo_e11=_semaforo(e11_avg),
            e24_dias_promedio=round(e24_avg, 1) if e24_avg is not None else None,
            e24_n=e24_n,
            semaforo_e24=_semaforo(e24_avg),
        ))

    return DemoraAreasResponse(anno=anno, areas=areas_out)
