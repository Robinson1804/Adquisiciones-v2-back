"""Pydantic v2 schemas for GET /tiempos/matriz response.

Mirrors the design contract from design artifact §Interfaces / Contracts.
Lists (not dicts) are used for columnas/celdas/promedios so the frontend
can zip them with columnas order without a key lookup.
"""
from __future__ import annotations

from pydantic import BaseModel


class ColumnaOut(BaseModel):
    key: str        # e.g. "cmn"
    label: str      # e.g. "CMN"
    cod: str | None  # e.g. "E01c"; None for TOTAL


class FilaMatrizOut(BaseModel):
    proceso_id: int
    id_proceso: str
    requerimiento: str
    pim: float | None          # proceso.pim as float; None when not set
    estado: str                # proceso.estado e.g. "EN PROCESO"
    tipo: str | None           # proceso.tipo e.g. "SERVICIO"; None when not set
    celdas: list[int | None]   # aligned to columnas order (excludes TOTAL); None = no data
    total_dias: int


class MatrizTiemposOut(BaseModel):
    columnas: list[ColumnaOut]            # fixed config, in order (including TOTAL at end)
    filas: list[FilaMatrizOut]
    promedios: list[float | None]         # avg per column (non-null cells only), aligned to columnas
    promedio_total: float | None          # avg of total_dias across all filas
