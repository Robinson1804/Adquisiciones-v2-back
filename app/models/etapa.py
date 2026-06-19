from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TIMESTAMP,
)
# NOTE: Boolean kept for es_bucle; String used for cmn_siga_confirmado tri-state
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.database import Base


class EtapaRegistro(Base):
    __tablename__ = "etapas_registro"
    __table_args__ = (
        CheckConstraint(
            "estado_etapa IN ('COMPLETADO','EN_CURSO','PENDIENTE','CANCELADO','OMITIDO','NO_APLICA')",
            name="ck_etapas_estado",
        ),
        CheckConstraint(
            "cmn_siga_confirmado IN ('SI','NO','EN_CURSO') OR cmn_siga_confirmado IS NULL",
            name="ck_etapas_cmn_siga_valido",
        ),
        Index("idx_etapas_proceso", "proceso_id"),
        Index("idx_etapas_codigo", "codigo_etapa"),
        # #1 — unicidad para etapas simples (no bucle, no por-área)
        # OMITIDO se excluye: reinicio TDR archiva la fila vieja como OMITIDO
        # y crea una nueva; ambas deben coexistir para auditoría.
        Index(
            "uq_etapa_simple_por_proceso",
            "proceso_id",
            "codigo_etapa",
            unique=True,
            postgresql_where=text(
                "es_bucle = false AND area_usuaria IS NULL AND estado_etapa != 'OMITIDO'"
            ),
        ),
        # #2 — unicidad para etapas por-área (E01, E11, E24)
        Index(
            "uq_etapa_por_area",
            "proceso_id",
            "codigo_etapa",
            "area_usuaria",
            unique=True,
            postgresql_where=text("area_usuaria IS NOT NULL"),
        ),
        # #9 — unicidad para rondas de bucle
        Index(
            "uq_ronda_bucle",
            "proceso_id",
            "codigo_etapa",
            "nro_ronda",
            unique=True,
            postgresql_where=text("es_bucle = true"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proceso_id: Mapped[int | None] = mapped_column(
        ForeignKey("procesos.id", ondelete="CASCADE")
    )
    codigo_etapa: Mapped[str] = mapped_column(String(10), nullable=False)
    nombre_etapa: Mapped[str] = mapped_column(Text, nullable=False)
    area_responsable: Mapped[str | None] = mapped_column(String(30))
    fecha_inicio: Mapped[date | None] = mapped_column(Date)
    fecha_fin: Mapped[date | None] = mapped_column(Date)
    # GENERATED ALWAYS AS (...) STORED — exact match to CONTEXT.md §4
    dias: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            "CASE WHEN fecha_fin IS NOT NULL AND fecha_inicio IS NOT NULL "
            "THEN fecha_fin - fecha_inicio ELSE NULL END",
            persisted=True,
        ),
    )
    # Loop fields
    es_bucle: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    nro_ronda: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    motivo_bucle: Mapped[str | None] = mapped_column(Text)
    # Stage-specific fields
    area_usuaria: Mapped[str | None] = mapped_column(String(100))
    monto_cert: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    resultado_eval: Mapped[str | None] = mapped_column(String(30))
    cmn_adjunto: Mapped[str | None] = mapped_column(String(20))
    nro_ocs: Mapped[str | None] = mapped_column(String(50))
    monto_ocs: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    plazo_entrega: Mapped[int | None] = mapped_column(Integer)
    fecha_envio_otpp: Mapped[date | None] = mapped_column(Date)
    fecha_resp_otpp: Mapped[date | None] = mapped_column(Date)
    # flujo-real-otin-v2 (migration 0008)
    fecha_limite_respuesta: Mapped[date | None] = mapped_column(Date, nullable=True)
    # migration 0009: tri-state VARCHAR(10) — 'SI' | 'NO' | 'EN_CURSO' | NULL
    cmn_siga_confirmado: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # migration 0009: editable round title (only meaningful when es_bucle=True)
    titulo_ronda: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Control fields
    responsable: Mapped[str | None] = mapped_column(String(150))
    oficio_correo: Mapped[str | None] = mapped_column(String(250))
    estado_etapa: Mapped[str] = mapped_column(String(20), server_default="PENDIENTE")
    observaciones: Mapped[str | None] = mapped_column(Text)
    registrado_por: Mapped[str | None] = mapped_column(String(100))
    registrado_en: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    actualizado_por: Mapped[str | None] = mapped_column(String(100))
    actualizado_en: Mapped[datetime | None] = mapped_column(TIMESTAMP)
