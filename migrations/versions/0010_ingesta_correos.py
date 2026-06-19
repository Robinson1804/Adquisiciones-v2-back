"""Ingesta de correos documental — tablas ingesta_correos e ingesta_documentos + procesos.numero_oficio.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-16

Cambios:
1. procesos: +numero_oficio VARCHAR(60) NULL + index idx_procesos_numero_oficio (no unique).
2. ingesta_correos: tabla nueva (staging correos extraídos por IA).
   - CHECK estado_revision IN ('PENDIENTE','APROBADO','APROBADO_AUTO','RECHAZADO')
   - UNIQUE entry_id (idempotencia dura por Outlook EntryID)
   - FK proceso_id → procesos(id) ON DELETE SET NULL
   - Índices: idx_ingesta_estado_revision, idx_ingesta_proceso_id
3. ingesta_documentos: tabla nueva (metadatos de adjuntos, patrón etapa_archivos).
   - FK ingesta_correo_id → ingesta_correos(id) ON DELETE CASCADE
   - FK proceso_id → procesos(id) ON DELETE SET NULL
   - Índices: idx_ingesta_docs_correo, idx_ingesta_docs_proceso

Reversible: downgrade() revierte en orden FK-seguro (docs → correos → columna).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. procesos: agregar numero_oficio + índice
    # ------------------------------------------------------------------
    op.add_column(
        "procesos",
        sa.Column("numero_oficio", sa.String(60), nullable=True),
    )
    op.create_index("idx_procesos_numero_oficio", "procesos", ["numero_oficio"])

    # ------------------------------------------------------------------
    # 2. ingesta_correos (staging)
    # ------------------------------------------------------------------
    op.create_table(
        "ingesta_correos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.String(512), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("sender_name", sa.String(255), nullable=True),
        sa.Column("sender_email", sa.String(255), nullable=True),
        sa.Column("received_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("body_clean", sa.Text(), nullable=True),
        # datos IA (editables por revisor)
        sa.Column("nombre_servicio", sa.Text(), nullable=True),
        sa.Column("nombre_servicio_normalizado", sa.Text(), nullable=True),
        sa.Column("numero_oficio_raw", sa.String(120), nullable=True),
        sa.Column("numero_oficio", sa.String(60), nullable=True),
        sa.Column("oss", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("siaf", sa.String(20), nullable=True),
        sa.Column("proveedor", sa.Text(), nullable=True),
        sa.Column("tipo", sa.String(10), nullable=True),
        sa.Column("fecha_documento", sa.Date(), nullable=True),
        sa.Column("fecha_recepcion", sa.Date(), nullable=True),
        # revisión
        sa.Column(
            "estado_revision",
            sa.String(15),
            nullable=False,
            server_default="PENDIENTE",
        ),
        sa.Column("match_confianza", sa.Numeric(4, 3), nullable=True),
        sa.Column("proceso_id", sa.Integer(), nullable=True),
        sa.Column("motivo_rechazo", sa.Text(), nullable=True),
        sa.Column("revisado_por", sa.String(100), nullable=True),
        sa.Column("revisado_en", sa.TIMESTAMP(), nullable=True),
        sa.Column(
            "creado_en",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", name="uq_ingesta_entry_id"),
        sa.CheckConstraint(
            "estado_revision IN ('PENDIENTE','APROBADO','APROBADO_AUTO','RECHAZADO')",
            name="ck_ingesta_estado_revision",
        ),
        sa.CheckConstraint(
            "tipo IS NULL OR tipo IN ('BIEN','SERVICIO')",
            name="ck_ingesta_tipo",
        ),
        sa.ForeignKeyConstraint(
            ["proceso_id"],
            ["procesos.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("idx_ingesta_estado_revision", "ingesta_correos", ["estado_revision"])
    op.create_index("idx_ingesta_proceso_id", "ingesta_correos", ["proceso_id"])

    # ------------------------------------------------------------------
    # 3. ingesta_documentos (patrón etapa_archivos — D2)
    # ------------------------------------------------------------------
    op.create_table(
        "ingesta_documentos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ingesta_correo_id", sa.Integer(), nullable=False),
        sa.Column("nombre_original", sa.String(255), nullable=False),
        sa.Column("nombre_almacenado", sa.String(255), nullable=False),
        sa.Column("ruta_relativa", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("tamano_bytes", sa.BigInteger(), nullable=False),
        sa.Column("tipo_clasificado", sa.String(20), nullable=True),
        sa.Column("confianza", sa.Numeric(4, 3), nullable=True),
        sa.Column("proceso_id", sa.Integer(), nullable=True),
        sa.Column(
            "creado_en",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        # FK → ingesta_correos CASCADE: borrar correo borra sus documentos
        sa.ForeignKeyConstraint(
            ["ingesta_correo_id"],
            ["ingesta_correos.id"],
            ondelete="CASCADE",
        ),
        # FK → procesos SET NULL: eliminar proceso no elimina los documentos
        sa.ForeignKeyConstraint(
            ["proceso_id"],
            ["procesos.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("idx_ingesta_docs_correo", "ingesta_documentos", ["ingesta_correo_id"])
    op.create_index("idx_ingesta_docs_proceso", "ingesta_documentos", ["proceso_id"])


def downgrade() -> None:
    # Orden FK-seguro: primero la tabla dependiente, luego la referenciada
    # ------------------------------------------------------------------
    # 3. ingesta_documentos
    # ------------------------------------------------------------------
    op.drop_index("idx_ingesta_docs_proceso", table_name="ingesta_documentos")
    op.drop_index("idx_ingesta_docs_correo", table_name="ingesta_documentos")
    op.drop_table("ingesta_documentos")

    # ------------------------------------------------------------------
    # 2. ingesta_correos
    # ------------------------------------------------------------------
    op.drop_index("idx_ingesta_proceso_id", table_name="ingesta_correos")
    op.drop_index("idx_ingesta_estado_revision", table_name="ingesta_correos")
    op.drop_table("ingesta_correos")

    # ------------------------------------------------------------------
    # 1. procesos: eliminar columna e índice
    # ------------------------------------------------------------------
    op.drop_index("idx_procesos_numero_oficio", table_name="procesos")
    op.drop_column("procesos", "numero_oficio")
