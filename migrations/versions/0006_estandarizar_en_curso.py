"""Estandarizar EN_CURSO: reemplazar 'EN CURSO' (espacio) por 'EN_CURSO' (guión bajo)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-27

El valor 'EN CURSO' con espacio era el valor nativo en la BD, pero el frontend
y el código de aplicación usan 'EN_CURSO' con guión bajo. El endpoint GET
tenía un normalizador espacio→guión que ocultaba el bug. Al hacer POST con
'EN_CURSO' se violaba el CHECK constraint → 500 Internal Server Error.

Solución: estandarizar TODO a 'EN_CURSO' (guión bajo) en BD y código.

Pasos:
1. UPDATE filas existentes 'EN CURSO' → 'EN_CURSO'.
2. DROP + CREATE del CHECK constraint con el nuevo valor.

Reversible: downgrade hace el camino inverso.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "etapas_registro"
_CONSTRAINT = "ck_etapas_estado"

_OLD_CHECK = (
    "estado_etapa IN ('COMPLETADO','EN CURSO','PENDIENTE','CANCELADO','OMITIDO','NO_APLICA')"
)
_NEW_CHECK = (
    "estado_etapa IN ('COMPLETADO','EN_CURSO','PENDIENTE','CANCELADO','OMITIDO','NO_APLICA')"
)


def upgrade() -> None:
    # 1. DROP primero (el UPDATE debe ejecutarse sin el constraint viejo activo)
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    # 2. Migrar datos: 'EN CURSO' → 'EN_CURSO'
    op.execute(
        "UPDATE etapas_registro SET estado_etapa = 'EN_CURSO' "
        "WHERE estado_etapa = 'EN CURSO'"
    )
    # 3. Crear el nuevo CHECK constraint con 'EN_CURSO'
    op.create_check_constraint(_CONSTRAINT, _TABLE, _NEW_CHECK)


def downgrade() -> None:
    # 1. DROP primero
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    # 2. Revertir datos: 'EN_CURSO' → 'EN CURSO'
    op.execute(
        "UPDATE etapas_registro SET estado_etapa = 'EN CURSO' "
        "WHERE estado_etapa = 'EN_CURSO'"
    )
    # 3. Restaurar el CHECK constraint original
    op.create_check_constraint(_CONSTRAINT, _TABLE, _OLD_CHECK)
