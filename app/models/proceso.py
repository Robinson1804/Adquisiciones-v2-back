from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, Integer, Numeric, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.database import Base


class Proceso(Base):
    __tablename__ = "procesos"
    __table_args__ = (
        CheckConstraint("tipo IN ('BIEN','SERVICIO')", name="ck_procesos_tipo"),
        CheckConstraint(
            "estado IN ('EN PROCESO','CULMINADO','CANCELADO')",
            name="ck_procesos_estado",
        ),
        Index("idx_procesos_anno", "anno"),
        Index("idx_procesos_estado", "estado"),
        Index("idx_procesos_eliminado_en", "eliminado_en"),
        # ingesta-correos-documental (migration 0010)
        Index("idx_procesos_numero_oficio", "numero_oficio"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    id_proceso: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    requerimiento: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str | None] = mapped_column(String(10))
    unidad_resp: Mapped[str | None] = mapped_column(String(100))
    areas_usuarias: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    pim: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    estado: Mapped[str] = mapped_column(String(20), server_default="EN PROCESO")
    motivo_cancel: Mapped[str | None] = mapped_column(Text)
    fecha_creacion: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    creado_por: Mapped[str | None] = mapped_column(String(100))
    anno: Mapped[int | None] = mapped_column(
        Integer, server_default=text("EXTRACT(YEAR FROM NOW())")
    )
    eliminado_en: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    # flujo-real-otin-v2 (migration 0008)
    denominacion_cmn: Mapped[str | None] = mapped_column(Text, nullable=True)
    clasificador_cmn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    area_iniciadora: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ingesta-correos-documental (migration 0010): ancla de trazabilidad documental
    numero_oficio: Mapped[str | None] = mapped_column(String(60), nullable=True)
