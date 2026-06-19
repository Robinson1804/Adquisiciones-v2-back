"""ORM model for etapa_archivos — file attachment metadata.

Each row references an etapas_registro row (etapa_id FK ON DELETE CASCADE).
The actual file lives on the filesystem at UPLOAD_DIR/ruta_relativa.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class EtapaArchivo(Base):
    __tablename__ = "etapa_archivos"
    __table_args__ = (Index("idx_archivos_etapa", "etapa_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    etapa_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("etapas_registro.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Display name — sanitized client filename (NEVER used in storage path)
    nombre_original: Mapped[str] = mapped_column(String(255), nullable=False)
    # UUID-based storage name (uuid4().hex + safe ext from MIME whitelist)
    nombre_almacenado: Mapped[str] = mapped_column(String(255), nullable=False)
    # Path relative to UPLOAD_DIR (e.g. "proc_5/abc123.pdf")
    ruta_relativa: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    tamano_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subido_por: Mapped[str | None] = mapped_column(String(100))
    subido_en: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now(), nullable=False
    )
