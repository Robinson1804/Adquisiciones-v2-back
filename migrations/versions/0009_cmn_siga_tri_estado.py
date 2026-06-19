"""cmn_siga_confirmado BOOLEAN → VARCHAR(10) tri-estado + titulo_ronda

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-29

Cambios:
1. etapas_registro.cmn_siga_confirmado: BOOLEAN NULL → VARCHAR(10) NULL
   - Mapeo de datos: true→'SI', false→'NO', NULL→NULL
   - CHECK constraint: valor IN ('SI','NO','EN_CURSO') OR NULL
2. etapas_registro: +titulo_ronda VARCHAR(200) NULL (aplica a rondas es_bucle=True)

Reversible via downgrade().
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Convertir cmn_siga_confirmado de BOOLEAN → VARCHAR(10)
    #    USING clause mapea: true→'SI', false→'NO', NULL→NULL
    # ------------------------------------------------------------------
    # Paso explícito: NULL→NULL (no-op, pero documenta intención)
    op.execute(
        "UPDATE etapas_registro SET cmn_siga_confirmado = NULL "
        "WHERE cmn_siga_confirmado IS NULL"
    )

    # ALTER con USING para mapear bool→string en una sola operación
    op.execute(
        "ALTER TABLE etapas_registro "
        "ALTER COLUMN cmn_siga_confirmado TYPE VARCHAR(10) "
        "USING CASE "
        "  WHEN cmn_siga_confirmado = TRUE  THEN 'SI' "
        "  WHEN cmn_siga_confirmado = FALSE THEN 'NO' "
        "  ELSE NULL "
        "END"
    )

    # ------------------------------------------------------------------
    # 2. Agregar CHECK constraint
    # ------------------------------------------------------------------
    op.create_check_constraint(
        "ck_etapas_cmn_siga_valido",
        "etapas_registro",
        "cmn_siga_confirmado IN ('SI','NO','EN_CURSO') OR cmn_siga_confirmado IS NULL",
    )

    # ------------------------------------------------------------------
    # 3. Nueva columna titulo_ronda
    # ------------------------------------------------------------------
    op.add_column(
        "etapas_registro",
        sa.Column("titulo_ronda", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Eliminar titulo_ronda
    # ------------------------------------------------------------------
    op.drop_column("etapas_registro", "titulo_ronda")

    # ------------------------------------------------------------------
    # 2. Drop CHECK constraint
    # ------------------------------------------------------------------
    op.drop_constraint(
        "ck_etapas_cmn_siga_valido",
        "etapas_registro",
        type_="check",
    )

    # ------------------------------------------------------------------
    # 3. Convertir VARCHAR(10) → BOOLEAN
    #    'SI'→true, 'NO'→false, 'EN_CURSO'→NULL (no representable en bool)
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE etapas_registro "
        "ALTER COLUMN cmn_siga_confirmado TYPE BOOLEAN "
        "USING CASE "
        "  WHEN cmn_siga_confirmado = 'SI'  THEN TRUE "
        "  WHEN cmn_siga_confirmado = 'NO'  THEN FALSE "
        "  ELSE NULL "
        "END"
    )
