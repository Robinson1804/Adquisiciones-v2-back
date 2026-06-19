"""SQLAlchemy ORM model for firma_secuencial table.

Tracks the sequential sign-off chain for E02b (V°B° TDR) and E06c (re-VB post-correction).
Each area must sign in strict orden order; all areas FIRMADO triggers E02b/E06c COMPLETADO.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.database import Base


class FirmaSecuencial(Base):
    __tablename__ = "firma_secuencial"
    __table_args__ = (
        UniqueConstraint(
            "proceso_id",
            "etapa_cod",
            "area",
            "ronda",
            name="uq_firma_secuencial_area_ronda",
        ),
        CheckConstraint(
            "estado IN ('PENDIENTE','RECIBIDO','FIRMADO','RECHAZADO')",
            name="ck_firma_secuencial_estado",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proceso_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("procesos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    etapa_cod: Mapped[str] = mapped_column(String(10), nullable=False)
    area: Mapped[str] = mapped_column(String(50), nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    estado: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'PENDIENTE'"),
    )
    fecha_recibido: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_firmado: Mapped[date | None] = mapped_column(Date, nullable=True)
    motivo_rechazo: Mapped[str | None] = mapped_column(Text, nullable=True)
    ronda: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
