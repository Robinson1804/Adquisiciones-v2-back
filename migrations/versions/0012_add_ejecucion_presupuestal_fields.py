"""Campos de ejecucion presupuestal para dashboard.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-24

Agrega campos opcionales para alinear el dashboard con Consulta Amigable:
- PIA
- Atencion de compromiso mensual
- Devengado
- Girado
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "montos_proceso",
        sa.Column("pia", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "montos_proceso",
        sa.Column("atencion_compromiso_mensual", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "montos_proceso",
        sa.Column("devengado", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "montos_proceso",
        sa.Column("girado", sa.Numeric(14, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("montos_proceso", "girado")
    op.drop_column("montos_proceso", "devengado")
    op.drop_column("montos_proceso", "atencion_compromiso_mensual")
    op.drop_column("montos_proceso", "pia")
