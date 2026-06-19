"""Pydantic v2 schemas for ingesta de correos documental.

Mirror estricto con los modelos SQLAlchemy en app/models/ingesta.py.
Estilo consistente con los schemas existentes (proceso.py, archivo.py).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Tipos de documentos clasificables
# ---------------------------------------------------------------------------

TipoDocumento = Literal[
    "TDR",
    "COTIZACION",
    "ORDEN_SERVICIO",
    "CONFORMIDAD",
    "INFORME",
    "OFICIO",
    "CRONOGRAMA",
    "SIAF",
    "OTRO",
]

EstadoRevision = Literal["PENDIENTE", "APROBADO", "APROBADO_AUTO", "RECHAZADO"]

# MIME types aceptados para adjuntos de ingesta (alineado con ALLOWED_CONTENT_TYPES de archivos_service)
_CONTENT_TYPES_PERMITIDOS: frozenset[str] = frozenset(
    [
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/tiff",
        "image/webp",
    ]
)


# ---------------------------------------------------------------------------
# Documento (adjunto extraído por el orquestador)
# ---------------------------------------------------------------------------


class DocumentoExtraidoIn(BaseModel):
    """Adjunto incluido en el payload del orquestador (binario en base64)."""

    nombre_original: str = Field(..., min_length=1, max_length=255)
    content_type: str
    tamano_bytes: int = Field(..., ge=0)
    contenido_b64: str = Field(..., min_length=1)  # base64 del binario
    tipo_clasificado: TipoDocumento
    confianza: float = Field(..., ge=0.0, le=1.0)

    from pydantic import field_validator

    @field_validator("content_type")
    @classmethod
    def _validar_content_type(cls, v: str) -> str:
        if v not in _CONTENT_TYPES_PERMITIDOS:
            raise ValueError(
                f"content_type '{v}' no está permitido. "
                f"Tipos válidos: {sorted(_CONTENT_TYPES_PERMITIDOS)}"
            )
        return v

    @field_validator("tamano_bytes")
    @classmethod
    def _tamano_positivo(cls, v: int) -> int:
        if v < 0:
            raise ValueError("tamano_bytes debe ser >= 0")
        return v


# ---------------------------------------------------------------------------
# Schemas de entrada (orquestador → backend)
# ---------------------------------------------------------------------------


class CorreoIngestaIn(BaseModel):
    """Payload completo de un correo extraído por el orquestador local."""

    entry_id: str = Field(..., min_length=1, max_length=512)
    subject: str | None = None
    sender_name: str | None = None
    sender_email: str | None = None
    received_at: datetime | None = None
    body_clean: str | None = None

    # Datos extraídos por IA
    nombre_servicio: str | None = None
    nombre_servicio_normalizado: str | None = None
    numero_oficio_raw: str | None = None   # valor crudo de la IA
    numero_oficio: str | None = None       # normalizado (puede ser None)
    oss: list[str] = Field(default_factory=list)
    siaf: str | None = None
    proveedor: str | None = None
    tipo: Literal["BIEN", "SERVICIO"] | None = None
    fecha_documento: date | None = None
    fecha_recepcion: date | None = None

    documentos: list[DocumentoExtraidoIn] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Schemas de salida (backend → frontend / orquestador)
# ---------------------------------------------------------------------------


class DocumentoIngestaOut(BaseModel):
    """Metadatos de un adjunto de ingesta (lectura)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ingesta_correo_id: int
    nombre_original: str
    nombre_almacenado: str
    ruta_relativa: str
    content_type: str
    tamano_bytes: int
    tipo_clasificado: str | None
    confianza: float | None
    proceso_id: int | None
    creado_en: datetime


class CorreoIngestaOut(BaseModel):
    """Correo de ingesta completo (lectura)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    entry_id: str
    subject: str | None
    sender_name: str | None
    sender_email: str | None
    received_at: datetime | None
    # Datos IA
    nombre_servicio: str | None
    nombre_servicio_normalizado: str | None
    numero_oficio_raw: str | None
    numero_oficio: str | None
    oss: list[str] | None
    siaf: str | None
    proveedor: str | None
    tipo: str | None
    fecha_documento: date | None
    fecha_recepcion: date | None
    # Revisión
    estado_revision: EstadoRevision
    match_confianza: float | None
    proceso_id: int | None
    motivo_rechazo: str | None
    revisado_por: str | None
    revisado_en: datetime | None
    creado_en: datetime
    # Documentos adjuntos (opcional: puede no venir en todos los endpoints)
    documentos: list[DocumentoIngestaOut] = Field(default_factory=list)


class IngestaPendientesOut(BaseModel):
    """Respuesta del GET /ingesta/pendientes."""

    items: list[CorreoIngestaOut]
    total: int


class IngestaCorreoResultOut(BaseModel):
    """Respuesta del POST /ingesta/correos (resultado de la ingesta + auto-vinculación)."""

    id: int
    estado_revision: EstadoRevision
    proceso_id: int | None = None
    match_confianza: float | None = None
    status: Literal["created", "auto_linked", "already_ingested"]


# ---------------------------------------------------------------------------
# Schemas de acción (bandeja de revisión)
# ---------------------------------------------------------------------------


class CorreoCorreccionIn(BaseModel):
    """PATCH parcial: corrección inline de un correo PENDIENTE por el revisor."""

    nombre_servicio: str | None = None
    nombre_servicio_normalizado: str | None = None
    numero_oficio: str | None = None
    numero_oficio_raw: str | None = None
    siaf: str | None = None
    proveedor: str | None = None
    tipo: Literal["BIEN", "SERVICIO"] | None = None
    fecha_documento: date | None = None
    fecha_recepcion: date | None = None


class AprobarIn(BaseModel):
    """Body del POST /ingesta/{id}/aprobar."""

    proceso_id: int = Field(..., gt=0)


class RechazarIn(BaseModel):
    """Body del POST /ingesta/{id}/rechazar."""

    motivo: str | None = None
