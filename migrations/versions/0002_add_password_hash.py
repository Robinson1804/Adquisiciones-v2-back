"""add password_hash to usuarios

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-25

Additive migration: adds password_hash VARCHAR(255) NOT NULL to usuarios.
Uses a temporary server_default="" so the column can be added NOT NULL even
if rows already exist; the server_default is removed in the same transaction
so no migration leaves an exposed default.  The seed script fills all real
rows after this migration runs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: add column with temporary server_default so existing rows satisfy NOT NULL
    op.add_column(
        "usuarios",
        sa.Column(
            "password_hash",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ),
    )
    # Step 2: remove the server_default — schema is clean, seed will fill real hashes
    op.alter_column("usuarios", "password_hash", server_default=None)


def downgrade() -> None:
    op.drop_column("usuarios", "password_hash")
