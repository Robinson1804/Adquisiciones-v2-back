"""Pydantic v2 schemas for the cross-process timing matrix."""
from __future__ import annotations

from pydantic import BaseModel


class ColumnaOut(BaseModel):
    key: str
    label: str
    cod: str | None


class FilaMatrizOut(BaseModel):
    proceso_id: int
    id_proceso: str
    requerimiento: str
    pim: float | None
    estado: str
    tipo: str | None
    celdas: list[int | None]
    total_dias: int


class MatrizTiemposOut(BaseModel):
    columnas: list[ColumnaOut]
    filas: list[FilaMatrizOut]
    promedios: list[float | None]
    promedio_total: float | None
