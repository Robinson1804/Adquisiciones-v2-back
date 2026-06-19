"""add NO_APLICA to ck_etapas_estado check constraint

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-27

Adds 'NO_APLICA' to the allowed values for etapas_registro.estado_etapa.
This supports direct-service-order flows that skip intermediate stages
(e.g. E08 → E19 bypassing E09-E18 budget certification block).

PostgreSQL requires dropping and re-creating the CHECK constraint because
ALTER TABLE … ALTER CONSTRAINT is not supported for CHECK constraints.
The column length is also widened from 20 → 20 (NO_APLICA = 9 chars, fits).
Reversible: downgrade restores the original 5-value constraint.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "etapas_registro"
_CONSTRAINT = "ck_etapas_estado"

_OLD_CHECK = (
    "estado_etapa IN ('COMPLETADO','EN CURSO','PENDIENTE','CANCELADO','OMITIDO')"
)
_NEW_CHECK = (
    "estado_etapa IN ('COMPLETADO','EN CURSO','PENDIENTE','CANCELADO','OMITIDO','NO_APLICA')"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _NEW_CHECK)


def downgrade() -> None:
    # Before reverting, ensure no rows have NO_APLICA (caller's responsibility)
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _OLD_CHECK)
