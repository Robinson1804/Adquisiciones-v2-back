"""Pydantic v2 schemas for procesos endpoints."""
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CmnPorArea(BaseModel):
    area: str
    cmn_adjunto: Literal["SI", "NO"] = "NO"


class ProcesoCreate(BaseModel):
    requerimiento: str = Field(min_length=3)
    tipo: Literal["BIEN", "SERVICIO"]
    unidad_resp: str | None = None  # ignored — always hardcoded to OTIN
    areas_usuarias: list[str] = Field(min_length=1)
    pim: Decimal | None = Field(default=None, ge=0)
    anno: int = Field(default_factory=lambda: datetime.now().year, ge=2020, le=2100)
    cmn_por_area: list[CmnPorArea] = []
    # When provided, E01a (Solicitud inicial área iniciadora) is auto-registered as
    # COMPLETADO with this date — the kickoff/anchor of the whole timeline.
    fecha_solicitud: date | None = None
    # flujo-real-otin-v2 — CMN compartido del proceso
    denominacion_cmn: str | None = None
    clasificador_cmn: str | None = None
    area_iniciadora: str | None = None

    @field_validator("areas_usuarias")
    @classmethod
    def _no_vacias(cls, v: list[str]) -> list[str]:
        if any(not a.strip() for a in v):
            raise ValueError("área vacía no permitida")
        return v


class ProcesoUpdate(BaseModel):
    """All fields optional — PATCH-like via PUT."""
    requerimiento: str | None = None
    tipo: Literal["BIEN", "SERVICIO"] | None = None
    unidad_resp: str | None = None
    areas_usuarias: list[str] | None = None
    pim: Decimal | None = None
    estado: Literal["EN PROCESO", "CULMINADO", "CANCELADO"] | None = None
    motivo_cancel: str | None = None
    anno: int | None = Field(default=None, ge=2020, le=2100)
    area_iniciadora: str | None = None
    denominacion_cmn: str | None = None
    clasificador_cmn: str | None = None


class ProcesoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    id_proceso: str
    requerimiento: str
    tipo: str | None
    unidad_resp: str | None
    areas_usuarias: list[str] | None
    pim: Decimal | None
    estado: str
    motivo_cancel: str | None
    fecha_creacion: datetime
    creado_por: str | None
    anno: int | None
    # flujo-real-otin-v2
    denominacion_cmn: str | None = None
    clasificador_cmn: str | None = None
    area_iniciadora: str | None = None
    # Alcance A — campos derivados de tiempos (TMP-01): NO son columnas en procesos.
    # Solo GET /procesos/{id} los rellena; list_procesos los deja en None.
    fecha_indagacion: Optional[date] = None
    fecha_transicion_ota: Optional[date] = None
    dias_transcurridos: Optional[int] = None
    # REFINAMIENTO FASE 3: etapa de mayor orden COMPLETADO — "dónde está el proceso".
    # None si no hay ninguna etapa COMPLETADO.
    etapa_actual_avance: Optional[str] = None


class PaginatedProcesos(BaseModel):
    items: list[ProcesoOut]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(
        cls,
        items: list[ProcesoOut],
        total: int,
        page: int,
        page_size: int,
    ) -> "PaginatedProcesos":
        pages = math.ceil(total / page_size) if page_size > 0 else 0
        return cls(items=items, total=total, page=page, page_size=page_size, pages=pages)


class OrdenServicioIn(BaseModel):
    """Wizard batch registration for O/S stages E14-E20."""
    fecha_os: date
    fechas_estimadas: dict[str, date] | None = None
    observaciones: str | None = None


class MontosOut(BaseModel):
    """montos_proceso row for the S4 ficha. Numeric fields as float to match
    the frontend MontosProceso type (number | null)."""
    model_config = ConfigDict(from_attributes=True)

    valor_em: float | None = None
    monto_cert_total: float | None = None
    nro_ocs: str | None = None
    monto_ocs: float | None = None
    plazo_entrega: int | None = None
    fecha_inicio_srv: date | None = None
