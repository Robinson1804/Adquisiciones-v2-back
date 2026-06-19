from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class HistorialCambio(Base):
    __tablename__ = "historial_cambios"

    id: Mapped[int] = mapped_column(primary_key=True)
    # No ON DELETE CASCADE — audit records are preserved even when process deleted
    proceso_id: Mapped[int | None] = mapped_column(ForeignKey("procesos.id"))
    etapa_id: Mapped[int | None] = mapped_column(ForeignKey("etapas_registro.id"))
    campo_modificado: Mapped[str | None] = mapped_column(String(100))
    valor_anterior: Mapped[str | None] = mapped_column(Text)
    valor_nuevo: Mapped[str | None] = mapped_column(Text)
    modificado_por: Mapped[str | None] = mapped_column(String(100))
    modificado_en: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
