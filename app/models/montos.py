from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class MontosProceso(Base):
    __tablename__ = "montos_proceso"

    id: Mapped[int] = mapped_column(primary_key=True)
    proceso_id: Mapped[int] = mapped_column(
        ForeignKey("procesos.id", ondelete="CASCADE"), unique=True
    )
    valor_em: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    monto_cert_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    nro_ocs: Mapped[str | None] = mapped_column(String(50))
    monto_ocs: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    plazo_entrega: Mapped[int | None] = mapped_column(Integer)
    fecha_inicio_srv: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
