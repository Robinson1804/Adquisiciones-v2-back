"""Pydantic schemas for file attachments (etapa_archivos)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ArchivoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    etapa_id: int
    nombre_original: str
    content_type: str
    tamano_bytes: int
    subido_por: str | None
    subido_en: datetime


class ArchivoListOut(BaseModel):
    archivos: list[ArchivoOut]
