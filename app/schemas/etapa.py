"""Pydantic v2 schemas for etapas endpoints.

Grouped GET response contract (Design D4):
  EtapasResponseOut {
    etapas: list[EtapaAgrupadaOut],
    progreso: ProgresoOut
  }

EtapaAgrupadaOut has:
  - filas: list[FilaAreaOut]  (simple stages + per-area stages)
  - rondas: list[RondaBucleOut]  (loop stages)
  - estado: COMPLETADO | EN_CURSO | PENDIENTE  (computed)
  - alerta_otpp: bool | None  (E16 only)
  - monto_total: Decimal | None  (E11 aggregate)
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CmnSiga = Literal["SI", "NO", "EN_CURSO"] | None


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

class EtapaCreate(BaseModel):
    """Payload for POST /procesos/{id}/etapas."""
    codigo_etapa: str
    nombre_etapa: str
    fecha_inicio: date | None = None
    estado_etapa: str = "PENDIENTE"
    fecha_fin: date | None = None
    responsable: str | None = None
    oficio_correo: str | None = None
    observaciones: str | None = None
    # Stage-specific optional fields
    area_usuaria: str | None = None
    cmn_adjunto: str | None = None
    monto_cert: Decimal | None = Field(default=None, ge=0)
    resultado_eval: str | None = None
    motivo_bucle: str | None = None
    fecha_envio_otpp: date | None = None
    fecha_resp_otpp: date | None = None
    nro_ocs: str | None = None
    monto_ocs: Decimal | None = Field(default=None, ge=0)
    plazo_entrega: int | None = Field(default=None, ge=0)
    # C3b: R2 — required when resultado_eval = 'SIN_PRESUPUESTO'
    motivo_cancel: str | None = None
    # flujo-real-otin-v2 (migration 0008)
    fecha_limite_respuesta: date | None = None
    # migration 0009: tri-state
    cmn_siga_confirmado: CmnSiga = None
    # migration 0009: editable round title
    titulo_ronda: str | None = None


class EtapaUpdate(BaseModel):
    """Payload for PUT /etapas/{id} — all fields optional (PATCH-style)."""
    nombre_etapa: str | None = None
    fecha_inicio: date | None = None
    estado_etapa: str | None = None
    fecha_fin: date | None = None
    responsable: str | None = None
    oficio_correo: str | None = None
    observaciones: str | None = None
    area_usuaria: str | None = None
    cmn_adjunto: str | None = None
    monto_cert: Decimal | None = Field(default=None, ge=0)
    resultado_eval: str | None = None
    motivo_bucle: str | None = None
    fecha_envio_otpp: date | None = None
    fecha_resp_otpp: date | None = None
    nro_ocs: str | None = None
    monto_ocs: Decimal | None = Field(default=None, ge=0)
    plazo_entrega: int | None = Field(default=None, ge=0)
    # flujo-real-otin-v2
    fecha_limite_respuesta: date | None = None
    # migration 0009: tri-state
    cmn_siga_confirmado: CmnSiga = None
    # migration 0009: editable round title
    titulo_ronda: str | None = None


class BucleCreate(BaseModel):
    """Payload for POST /procesos/{id}/etapas/{cod}/bucle."""
    motivo_bucle: str


# ---------------------------------------------------------------------------
# Output schemas — single row
# ---------------------------------------------------------------------------

class EtapaOut(BaseModel):
    """Single-row response for POST and PUT endpoints."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    proceso_id: int | None
    codigo_etapa: str
    nombre_etapa: str
    nro_ronda: int
    es_bucle: bool
    area_usuaria: str | None
    estado_etapa: str
    fecha_inicio: date | None
    fecha_fin: date | None
    dias: int | None
    responsable: str | None
    oficio_correo: str | None
    observaciones: str | None
    cmn_adjunto: str | None
    monto_cert: Decimal | None
    resultado_eval: str | None
    motivo_bucle: str | None
    fecha_envio_otpp: date | None
    fecha_resp_otpp: date | None
    nro_ocs: str | None
    monto_ocs: Decimal | None
    plazo_entrega: int | None
    registrado_por: str | None
    # Derived — never stored; computed on read when codigo_etapa == 'E19'
    vencimiento_ocs: date | None = None
    # flujo-real-otin-v2
    fecha_limite_respuesta: date | None = None
    # migration 0009: tri-state
    cmn_siga_confirmado: CmnSiga = None
    # migration 0009: editable round title
    titulo_ronda: str | None = None


# ---------------------------------------------------------------------------
# Output schemas — grouped GET response (Design D4 contract)
# ---------------------------------------------------------------------------

class FilaAreaOut(BaseModel):
    """One row in the filas[] array of EtapaAgrupadaOut."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    area_usuaria: str | None
    estado_etapa: str
    fecha_inicio: date | None
    fecha_fin: date | None
    dias: int | None
    cmn_adjunto: str | None
    monto_cert: Decimal | None
    resultado_eval: str | None
    nro_ocs: str | None
    monto_ocs: Decimal | None
    plazo_entrega: int | None
    fecha_envio_otpp: date | None
    fecha_resp_otpp: date | None
    responsable: str | None
    oficio_correo: str | None
    observaciones: str | None
    registrado_por: str | None
    # Derived for E19
    vencimiento_ocs: date | None = None


class RondaBucleOut(BaseModel):
    """One round entry in rondas[] for loop-type stages."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    nro_ronda: int
    motivo_bucle: str | None
    estado_etapa: str
    fecha_inicio: date | None
    fecha_fin: date | None
    dias: int | None
    # migration 0009: editable round title
    titulo_ronda: str | None = None


class EtapaAgrupadaOut(BaseModel):
    """One stage group in the GET /procesos/{id}/etapas response.

    - Simple stages: filas has 0..1 entries, rondas is [].
    - Loop stages (es_bucle=True): rondas has 0..N entries, filas is [].
    - Per-area stages (por_area=True): filas has one entry per area.
    """
    cod: str
    nombre: str
    area_responsable: str
    es_bucle: bool
    por_area: bool
    estado: str  # COMPLETADO | EN_CURSO | PENDIENTE
    filas: list[FilaAreaOut] = []
    rondas: list[RondaBucleOut] = []
    alerta_otpp: bool | None = None   # E16 only
    monto_total: Decimal | None = None  # E11 aggregate


class ProgresoOut(BaseModel):
    """Progress summary embedded in EtapasResponseOut."""
    etapa_actual: str | None  # first non-COMPLETADO cod in ORDEN_ETAPAS
    porcentaje: float
    completadas: int
    total: int  # always 25 per Design D2


class EtapasResponseOut(BaseModel):
    """Full response for GET /procesos/{id}/etapas (Design D4 contract)."""
    etapas: list[EtapaAgrupadaOut]
    progreso: ProgresoOut


# ---------------------------------------------------------------------------
# Output schemas — timing analysis (GET /procesos/{id}/tiempos)
# ---------------------------------------------------------------------------

class IntervaloOut(BaseModel):
    """One interval between two consecutive dated milestones."""
    cod: str
    nombre: str
    area_responsable: str
    desde: date
    hasta: date
    dias: int


class AreaTiempoOut(BaseModel):
    """Aggregated days per responsible area."""
    area: str
    dias_total: int


class CuelloBotellaOut(BaseModel):
    """The interval with the highest day count."""
    cod: str
    dias: int


class TiemposProcesoOut(BaseModel):
    """Full response for GET /procesos/{id}/tiempos."""
    intervalos: list[IntervaloOut]
    total_dias: int
    por_area: list[AreaTiempoOut]
    cuello_de_botella: CuelloBotellaOut | None
