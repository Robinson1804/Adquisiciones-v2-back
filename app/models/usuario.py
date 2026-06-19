from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, String, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.database import Base


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint("rol IN ('ADMIN','EDITOR','VIEWER')", name="ck_usuarios_rol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    nombre_completo: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str | None] = mapped_column(String(150))
    area: Mapped[str | None] = mapped_column(String(100))
    rol: Mapped[str] = mapped_column(String(20), server_default="EDITOR")
    activo: Mapped[bool] = mapped_column(Boolean, server_default=text("TRUE"))
    creado_en: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
