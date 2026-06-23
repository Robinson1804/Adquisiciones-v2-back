"""Campos de sugerencia para ingesta Exchange/MCP.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-18

Agrega metadatos no decisorios para mostrar en /ingesta:
- relevancia_score / relevancia_motivos
- proceso_sugerido_id
- etapa_sugerida / fase_sugerida
- resumen_sugerido
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ingesta_correos",
        sa.Column("relevancia_score", sa.Numeric(4, 3), nullable=True),
    )
    op.add_column(
        "ingesta_correos",
        sa.Column("relevancia_motivos", sa.Text(), nullable=True),
    )
    op.add_column(
        "ingesta_correos",
        sa.Column("proceso_sugerido_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ingesta_correos",
        sa.Column("etapa_sugerida", sa.String(10), nullable=True),
    )
    op.add_column(
        "ingesta_correos",
        sa.Column("fase_sugerida", sa.String(80), nullable=True),
    )
    op.add_column(
        "ingesta_correos",
        sa.Column("resumen_sugerido", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ingesta_proceso_sugerido_id",
        "ingesta_correos",
        "procesos",
        ["proceso_sugerido_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_ingesta_proceso_sugerido_id",
        "ingesta_correos",
        ["proceso_sugerido_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_ingesta_proceso_sugerido_id", table_name="ingesta_correos")
    op.drop_constraint(
        "fk_ingesta_proceso_sugerido_id",
        "ingesta_correos",
        type_="foreignkey",
    )
    op.drop_column("ingesta_correos", "resumen_sugerido")
    op.drop_column("ingesta_correos", "fase_sugerida")
    op.drop_column("ingesta_correos", "etapa_sugerida")
    op.drop_column("ingesta_correos", "proceso_sugerido_id")
    op.drop_column("ingesta_correos", "relevancia_motivos")
    op.drop_column("ingesta_correos", "relevancia_score")
