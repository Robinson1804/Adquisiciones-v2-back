"""add etapa_archivos table for file attachments on key stages

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-25

Additive migration: creates etapa_archivos table with FK → etapas_registro ON DELETE CASCADE.
Files are stored on the filesystem; this table holds metadata only.
Reversible.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "etapa_archivos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("etapa_id", sa.Integer(), nullable=False),
        sa.Column("nombre_original", sa.String(255), nullable=False),
        sa.Column("nombre_almacenado", sa.String(255), nullable=False),
        sa.Column("ruta_relativa", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("tamano_bytes", sa.BigInteger(), nullable=False),
        sa.Column("subido_por", sa.String(100), nullable=True),
        sa.Column(
            "subido_en",
            sa.TIMESTAMP(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["etapa_id"],
            ["etapas_registro.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_archivos_etapa", "etapa_archivos", ["etapa_id"])


def downgrade() -> None:
    op.drop_index("idx_archivos_etapa", table_name="etapa_archivos")
    op.drop_table("etapa_archivos")
