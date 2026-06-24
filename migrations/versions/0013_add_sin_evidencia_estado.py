"""Estado SIN_EVIDENCIA para etapas inferidas.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-24

Permite distinguir etapas completadas con soporte documental de etapas
completadas por inferencia cuando se registra una etapa posterior.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "etapas_registro"
_CONSTRAINT = "ck_etapas_estado"
_OLD_CHECK = (
    "estado_etapa IN ('COMPLETADO','EN_CURSO','PENDIENTE',"
    "'CANCELADO','OMITIDO','NO_APLICA')"
)
_NEW_CHECK = (
    "estado_etapa IN ('COMPLETADO','EN_CURSO','PENDIENTE',"
    "'CANCELADO','OMITIDO','NO_APLICA','SIN_EVIDENCIA')"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _NEW_CHECK)


def downgrade() -> None:
    op.execute(
        "UPDATE etapas_registro "
        "SET estado_etapa = 'COMPLETADO' "
        "WHERE estado_etapa = 'SIN_EVIDENCIA'"
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _OLD_CHECK)
