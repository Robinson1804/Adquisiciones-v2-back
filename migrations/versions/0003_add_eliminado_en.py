"""add eliminado_en to procesos (soft delete)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-25

Additive migration: adds procesos.eliminado_en TIMESTAMP NULL for soft delete.
NULL = active, non-NULL = soft-deleted. Reversible.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "procesos",
        sa.Column("eliminado_en", sa.TIMESTAMP(), nullable=True),
    )
    op.create_index("idx_procesos_eliminado_en", "procesos", ["eliminado_en"])


def downgrade() -> None:
    op.drop_index("idx_procesos_eliminado_en", table_name="procesos")
    op.drop_column("procesos", "eliminado_en")
