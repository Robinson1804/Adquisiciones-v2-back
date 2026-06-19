"""Pydantic v2 schemas for the executive dashboard endpoints (C4).

All monetary fields use float | None (consistent with MontosOut from C3b #138).
Field names match the design-authoritative definitions in design #152 §1.2.
"""
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# GET /dashboard/metricas
# ---------------------------------------------------------------------------

class MetricasOut(BaseModel):
    anno: int
    total: int
    en_proceso: int
    culminados: int
    cancelados: int
    pim_total: float | None
    dias_promedio: float | None


# ---------------------------------------------------------------------------
# GET /dashboard/flujo-procesos
# ---------------------------------------------------------------------------

class FaseProgresoOut(BaseModel):
    fase: str        # F1..F5
    label: str
    completada: bool
    actual: bool


class ProcesoFlujoOut(BaseModel):
    id: int
    id_proceso: str
    requerimiento: str
    estado: str
    fase_actual: str | None
    porcentaje: float
    fases: list[FaseProgresoOut]


class FlujoProcesosResponse(BaseModel):
    anno: int
    procesos: list[ProcesoFlujoOut]


# ---------------------------------------------------------------------------
# GET /dashboard/tiempos-etapa
# ---------------------------------------------------------------------------

class TiempoEtapaOut(BaseModel):
    codigo: str
    nombre: str
    area_responsable: str | None
    dias_promedio: float | None
    n: int


class TiemposEtapaResponse(BaseModel):
    anno: int
    promedio_global: float | None
    etapas: list[TiempoEtapaOut]


# ---------------------------------------------------------------------------
# GET /dashboard/presupuesto
# ---------------------------------------------------------------------------

class PresupuestoProcesoOut(BaseModel):
    id: int
    id_proceso: str
    requerimiento: str
    estado: str
    pim: float | None
    valor_em: float | None
    monto_cert_total: float | None
    monto_ocs: float | None
    var_em_vs_pim: float | None
    var_cert_vs_em: float | None
    var_ocs_vs_em: float | None


class PresupuestoResponse(BaseModel):
    anno: int
    totales: dict[str, float | None]   # pim, valor_em, monto_cert_total, monto_ocs
    procesos: list[PresupuestoProcesoOut]


# ---------------------------------------------------------------------------
# GET /dashboard/demora-areas
# ---------------------------------------------------------------------------

class DemoraAreaOut(BaseModel):
    area_usuaria: str
    e11_dias_promedio: float | None
    e11_n: int
    semaforo_e11: str | None
    e24_dias_promedio: float | None
    e24_n: int
    semaforo_e24: str | None


class DemoraAreasResponse(BaseModel):
    anno: int
    areas: list[DemoraAreaOut]
