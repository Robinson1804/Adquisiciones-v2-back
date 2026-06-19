"""initial schema — 5 tables (usuarios, procesos, etapas_registro, montos_proceso, historial_cambios)

Revision ID: 0001
Revises:
Create Date: 2026-05-25

HAND-WRITTEN: Autogenerate cannot reliably reproduce the Computed (GENERATED ALWAYS AS ... STORED)
column on etapas_registro.dias, the postgresql ARRAY type on procesos.areas_usuarias, or the
CHECK constraints. This migration is the authoritative DDL matching CONTEXT.md §4 exactly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. usuarios
    # ------------------------------------------------------------------
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("nombre_completo", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=150), nullable=True),
        sa.Column("area", sa.String(length=100), nullable=True),
        sa.Column(
            "rol",
            sa.String(length=20),
            server_default="EDITOR",
            nullable=False,
        ),
        sa.Column(
            "activo",
            sa.Boolean(),
            server_default=sa.text("TRUE"),
            nullable=False,
        ),
        sa.Column(
            "creado_en",
            sa.TIMESTAMP(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rol IN ('ADMIN','EDITOR','VIEWER')", name="ck_usuarios_rol"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    # ------------------------------------------------------------------
    # 2. procesos
    # ------------------------------------------------------------------
    op.create_table(
        "procesos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id_proceso", sa.String(length=20), nullable=False),
        sa.Column("requerimiento", sa.Text(), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=True),
        sa.Column("unidad_resp", sa.String(length=100), nullable=True),
        sa.Column(
            "areas_usuarias",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        sa.Column("pim", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "estado",
            sa.String(length=20),
            server_default="EN PROCESO",
            nullable=False,
        ),
        sa.Column("motivo_cancel", sa.Text(), nullable=True),
        sa.Column(
            "fecha_creacion",
            sa.TIMESTAMP(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("creado_por", sa.String(length=100), nullable=True),
        sa.Column(
            "anno",
            sa.Integer(),
            server_default=sa.text("EXTRACT(YEAR FROM NOW())"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "tipo IN ('BIEN','SERVICIO')", name="ck_procesos_tipo"
        ),
        sa.CheckConstraint(
            "estado IN ('EN PROCESO','CULMINADO','CANCELADO')",
            name="ck_procesos_estado",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_proceso"),
    )
    op.create_index("idx_procesos_anno", "procesos", ["anno"])
    op.create_index("idx_procesos_estado", "procesos", ["estado"])

    # ------------------------------------------------------------------
    # 3. etapas_registro  (includes the GENERATED ALWAYS AS ... STORED column)
    # ------------------------------------------------------------------
    op.create_table(
        "etapas_registro",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("proceso_id", sa.Integer(), nullable=True),
        sa.Column("codigo_etapa", sa.String(length=10), nullable=False),
        sa.Column("nombre_etapa", sa.Text(), nullable=False),
        sa.Column("area_responsable", sa.String(length=30), nullable=True),
        sa.Column("fecha_inicio", sa.Date(), nullable=True),
        sa.Column("fecha_fin", sa.Date(), nullable=True),
        # GENERATED ALWAYS AS (...) STORED — exact expression from CONTEXT.md §4
        # Note: fecha_fin and fecha_inicio are already DATE, so no ::date cast needed;
        # subtracting two DATEs in Postgres yields INTEGER (days).
        sa.Column(
            "dias",
            sa.Integer(),
            sa.Computed(
                "CASE WHEN fecha_fin IS NOT NULL AND fecha_inicio IS NOT NULL "
                "THEN fecha_fin - fecha_inicio ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
        # Loop fields
        sa.Column(
            "es_bucle",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
        ),
        sa.Column(
            "nro_ronda",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("motivo_bucle", sa.Text(), nullable=True),
        # Stage-specific fields
        sa.Column("area_usuaria", sa.String(length=100), nullable=True),
        sa.Column("monto_cert", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("resultado_eval", sa.String(length=30), nullable=True),
        sa.Column("cmn_adjunto", sa.String(length=20), nullable=True),
        sa.Column("nro_ocs", sa.String(length=50), nullable=True),
        sa.Column("monto_ocs", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("plazo_entrega", sa.Integer(), nullable=True),
        sa.Column("fecha_envio_otpp", sa.Date(), nullable=True),
        sa.Column("fecha_resp_otpp", sa.Date(), nullable=True),
        # Control fields
        sa.Column("responsable", sa.String(length=150), nullable=True),
        sa.Column("oficio_correo", sa.String(length=250), nullable=True),
        sa.Column(
            "estado_etapa",
            sa.String(length=20),
            server_default="PENDIENTE",
            nullable=False,
        ),
        sa.Column("observaciones", sa.Text(), nullable=True),
        sa.Column("registrado_por", sa.String(length=100), nullable=True),
        sa.Column(
            "registrado_en",
            sa.TIMESTAMP(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actualizado_por", sa.String(length=100), nullable=True),
        sa.Column("actualizado_en", sa.TIMESTAMP(), nullable=True),
        sa.CheckConstraint(
            "estado_etapa IN ('COMPLETADO','EN CURSO','PENDIENTE','CANCELADO','OMITIDO')",
            name="ck_etapas_estado",
        ),
        sa.ForeignKeyConstraint(
            ["proceso_id"],
            ["procesos.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_etapas_proceso", "etapas_registro", ["proceso_id"])
    op.create_index("idx_etapas_codigo", "etapas_registro", ["codigo_etapa"])

    # ------------------------------------------------------------------
    # 4. montos_proceso
    # ------------------------------------------------------------------
    op.create_table(
        "montos_proceso",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("proceso_id", sa.Integer(), nullable=False),
        sa.Column("valor_em", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "monto_cert_total", sa.Numeric(precision=14, scale=2), nullable=True
        ),
        sa.Column("nro_ocs", sa.String(length=50), nullable=True),
        sa.Column("monto_ocs", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("plazo_entrega", sa.Integer(), nullable=True),
        sa.Column("fecha_inicio_srv", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["proceso_id"],
            ["procesos.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proceso_id"),
    )

    # ------------------------------------------------------------------
    # 5. historial_cambios  (FKs intentionally without CASCADE — audit trail)
    # ------------------------------------------------------------------
    op.create_table(
        "historial_cambios",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("proceso_id", sa.Integer(), nullable=True),
        sa.Column("etapa_id", sa.Integer(), nullable=True),
        sa.Column("campo_modificado", sa.String(length=100), nullable=True),
        sa.Column("valor_anterior", sa.Text(), nullable=True),
        sa.Column("valor_nuevo", sa.Text(), nullable=True),
        sa.Column("modificado_por", sa.String(length=100), nullable=True),
        sa.Column(
            "modificado_en",
            sa.TIMESTAMP(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # No ON DELETE CASCADE on either FK — preserve audit records
        sa.ForeignKeyConstraint(["proceso_id"], ["procesos.id"]),
        sa.ForeignKeyConstraint(["etapa_id"], ["etapas_registro.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("historial_cambios")
    op.drop_table("montos_proceso")
    op.drop_index("idx_etapas_codigo", table_name="etapas_registro")
    op.drop_index("idx_etapas_proceso", table_name="etapas_registro")
    op.drop_table("etapas_registro")
    op.drop_index("idx_procesos_estado", table_name="procesos")
    op.drop_index("idx_procesos_anno", table_name="procesos")
    op.drop_table("procesos")
    op.drop_table("usuarios")
