"""ORM models for ingesta de correos documental (migration 0010).

IngestaCorreo — staging de correos extraídos por IA, pendientes de revisión.
IngestaDocumento — metadatos de adjuntos vinculados a un correo de ingesta.

Patrón: mapped_column con SQLAlchemy 2.0, igual que los modelos existentes.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class IngestaCorreo(Base):
    """Correo extraído por la IA en staging, pendiente de revisión/vinculación."""

    __tablename__ = "ingesta_correos"
    __table_args__ = (
        CheckConstraint(
            "estado_revision IN ('PENDIENTE','APROBADO','APROBADO_AUTO','RECHAZADO')",
            name="ck_ingesta_estado_revision",
        ),
        CheckConstraint(
            "tipo IS NULL OR tipo IN ('BIEN','SERVICIO')",
            name="ck_ingesta_tipo",
        ),
        # Idempotencia dura: un registro por correo (Outlook EntryID)
        # Definida explícitamente como UniqueConstraint para control de nombre
        Index("uq_ingesta_entry_id", "entry_id", unique=True),
        Index("idx_ingesta_estado_revision", "estado_revision"),
        Index("idx_ingesta_proceso_id", "proceso_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Clave de dedup: Outlook EntryID (estable mientras el correo no cambie de store)
    entry_id: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)

    # Cabeceras del correo
    subject: Mapped[str | None] = mapped_column(Text)
    sender_name: Mapped[str | None] = mapped_column(String(255))
    sender_email: Mapped[str | None] = mapped_column(String(255))
    received_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    body_clean: Mapped[str | None] = mapped_column(Text)

    # Datos extraídos por IA (todos editables por el revisor antes de aprobar)
    nombre_servicio: Mapped[str | None] = mapped_column(Text)
    nombre_servicio_normalizado: Mapped[str | None] = mapped_column(Text)
    numero_oficio_raw: Mapped[str | None] = mapped_column(String(120))  # crudo para auditoría
    numero_oficio: Mapped[str | None] = mapped_column(String(60))       # normalizado (puede ser NULL)
    oss: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    siaf: Mapped[str | None] = mapped_column(String(20))
    proveedor: Mapped[str | None] = mapped_column(Text)
    tipo: Mapped[str | None] = mapped_column(String(10))                # BIEN|SERVICIO
    fecha_documento: Mapped[date | None] = mapped_column(Date)
    fecha_recepcion: Mapped[date | None] = mapped_column(Date)

    # Estado de revisión
    estado_revision: Mapped[str] = mapped_column(
        String(15),
        nullable=False,
        server_default="PENDIENTE",
    )
    # Confianza del matching difuso de nombre de servicio (solo en APROBADO_AUTO)
    match_confianza: Mapped[float | None] = mapped_column(Numeric(4, 3))

    # Vinculación al proceso (se setea al aprobar / auto-vincular)
    proceso_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("procesos.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Auditoría de revisión
    motivo_rechazo: Mapped[str | None] = mapped_column(Text)
    revisado_por: Mapped[str | None] = mapped_column(String(100))   # username o "INGESTA_AUTO"
    revisado_en: Mapped[datetime | None] = mapped_column(TIMESTAMP)

    creado_en: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now(), nullable=False
    )


class IngestaDocumento(Base):
    """Adjunto de un correo de ingesta.

    Reutiliza el patrón exacto de EtapaArchivo (D2, ADR-3):
    UUID storage, nunca nombre del cliente en la ruta.
    """

    __tablename__ = "ingesta_documentos"
    __table_args__ = (
        Index("idx_ingesta_docs_correo", "ingesta_correo_id"),
        Index("idx_ingesta_docs_proceso", "proceso_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # FK al correo — CASCADE: borrar el correo borra sus documentos
    ingesta_correo_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ingesta_correos.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Metadatos del archivo (patrón etapa_archivos, D2)
    nombre_original: Mapped[str] = mapped_column(String(255), nullable=False)   # display, sanitizado
    nombre_almacenado: Mapped[str] = mapped_column(String(255), nullable=False)  # uuid4().hex + ext segura
    ruta_relativa: Mapped[str] = mapped_column(String(500), nullable=False)      # relativa a UPLOAD_DIR
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    tamano_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Clasificación del documento (editable por revisor)
    tipo_clasificado: Mapped[str | None] = mapped_column(String(20))   # TDR|CONFORMIDAD|...
    confianza: Mapped[float | None] = mapped_column(Numeric(4, 3))    # confianza de clasificación IA

    # FK al proceso (se setea al vincular, se limpia al desvincular)
    proceso_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("procesos.id", ondelete="SET NULL"),
        nullable=True,
    )

    creado_en: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now(), nullable=False
    )
