"""Flujo real OTIN v2 — migration 0008

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-29

Cambios:
1. procesos: +denominacion_cmn TEXT, +clasificador_cmn VARCHAR(20), +area_iniciadora TEXT
2. etapas_registro: +fecha_limite_respuesta DATE, +cmn_siga_confirmado BOOLEAN
3. Tabla nueva: firma_secuencial (V°B° secuencial áreas TDR)
4. Data migration: etapas_registro SET codigo_etapa='E01c' WHERE codigo_etapa='E01'

Reversible via downgrade().
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Nuevas columnas en procesos
    # ------------------------------------------------------------------
    op.add_column("procesos", sa.Column("denominacion_cmn", sa.Text(), nullable=True))
    op.add_column("procesos", sa.Column("clasificador_cmn", sa.String(20), nullable=True))
    op.add_column("procesos", sa.Column("area_iniciadora", sa.Text(), nullable=True))

    # ------------------------------------------------------------------
    # 2. Nuevas columnas en etapas_registro
    # ------------------------------------------------------------------
    op.add_column(
        "etapas_registro",
        sa.Column("fecha_limite_respuesta", sa.Date(), nullable=True),
    )
    op.add_column(
        "etapas_registro",
        sa.Column("cmn_siga_confirmado", sa.Boolean(), nullable=True),
    )

    # ------------------------------------------------------------------
    # 3. Crear tabla firma_secuencial
    # ------------------------------------------------------------------
    op.create_table(
        "firma_secuencial",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proceso_id", sa.Integer(), nullable=False),
        sa.Column("etapa_cod", sa.String(10), nullable=False),
        sa.Column("area", sa.String(50), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False, server_default="PENDIENTE"),
        sa.Column("fecha_recibido", sa.Date(), nullable=True),
        sa.Column("fecha_firmado", sa.Date(), nullable=True),
        sa.Column("motivo_rechazo", sa.Text(), nullable=True),
        sa.Column("ronda", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(
            ["proceso_id"],
            ["procesos.id"],
            ondelete="CASCADE",
            name="fk_firma_secuencial_proceso",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_firma_secuencial"),
        sa.UniqueConstraint(
            "proceso_id",
            "etapa_cod",
            "area",
            "ronda",
            name="uq_firma_secuencial_area_ronda",
        ),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE','RECIBIDO','FIRMADO','RECHAZADO')",
            name="ck_firma_secuencial_estado",
        ),
    )
    op.create_index(
        "idx_firma_secuencial_proceso",
        "firma_secuencial",
        ["proceso_id"],
    )

    # ------------------------------------------------------------------
    # 4. Data migration: E01 → E01c
    # ------------------------------------------------------------------
    op.execute(
        "UPDATE etapas_registro SET codigo_etapa = 'E01c' WHERE codigo_etapa = 'E01'"
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Reverse data migration first: E01c → E01
    # Only safe if cmn_siga_confirmado column still exists (it does during downgrade)
    # ------------------------------------------------------------------
    op.execute(
        "UPDATE etapas_registro SET codigo_etapa = 'E01' WHERE codigo_etapa = 'E01c'"
    )

    # ------------------------------------------------------------------
    # Drop firma_secuencial table
    # ------------------------------------------------------------------
    op.drop_index("idx_firma_secuencial_proceso", table_name="firma_secuencial")
    op.drop_table("firma_secuencial")

    # ------------------------------------------------------------------
    # Drop etapas_registro columns
    # ------------------------------------------------------------------
    op.drop_column("etapas_registro", "cmn_siga_confirmado")
    op.drop_column("etapas_registro", "fecha_limite_respuesta")

    # ------------------------------------------------------------------
    # Drop procesos columns
    # ------------------------------------------------------------------
    op.drop_column("procesos", "area_iniciadora")
    op.drop_column("procesos", "clasificador_cmn")
    op.drop_column("procesos", "denominacion_cmn")
