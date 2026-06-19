"""Índices únicos parciales en etapas_registro + limpieza de duplicados/huérfanos

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-28

Cambios:
1. Limpia filas huérfanas (proceso soft-deleted) y duplicados en procesos activos.
2. Crea 3 índices únicos parciales:
   - uq_etapa_simple_por_proceso: (proceso_id, codigo_etapa) WHERE NOT es_bucle AND area_usuaria IS NULL
   - uq_etapa_por_area:           (proceso_id, codigo_etapa, area_usuaria) WHERE area_usuaria IS NOT NULL
   - uq_ronda_bucle:              (proceso_id, codigo_etapa, nro_ronda) WHERE es_bucle = true

Downgrade: solo borra los índices. Los datos eliminados en upgrade NO se restauran.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Limpiar filas huérfanas (proceso con eliminado_en IS NOT NULL)
    #    Primero borramos historial_cambios que referencia esas etapas
    # ------------------------------------------------------------------
    op.execute(
        """
        DELETE FROM historial_cambios
        WHERE etapa_id IN (
            SELECT er.id FROM etapas_registro er
            JOIN procesos p ON p.id = er.proceso_id
            WHERE p.eliminado_en IS NOT NULL
        )
        """
    )
    op.execute(
        """
        DELETE FROM etapas_registro
        WHERE proceso_id IN (
            SELECT id FROM procesos WHERE eliminado_en IS NOT NULL
        )
        """
    )

    # ------------------------------------------------------------------
    # 2. Eliminar duplicados de etapas simples en procesos activos
    #    Conservar solo MAX(id) por (proceso_id, codigo_etapa)
    # ------------------------------------------------------------------
    op.execute(
        """
        DELETE FROM historial_cambios
        WHERE etapa_id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY proceso_id, codigo_etapa
                           ORDER BY id DESC
                       ) AS rn
                FROM etapas_registro
                WHERE NOT es_bucle AND area_usuaria IS NULL
            ) ranked
            WHERE rn > 1
        )
        """
    )
    op.execute(
        """
        DELETE FROM etapas_registro
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY proceso_id, codigo_etapa
                           ORDER BY id DESC
                       ) AS rn
                FROM etapas_registro
                WHERE NOT es_bucle
                  AND area_usuaria IS NULL
            ) ranked
            WHERE rn > 1
        )
        """
    )

    # ------------------------------------------------------------------
    # 3. Eliminar duplicados por-área en procesos activos
    #    Conservar solo MAX(id) por (proceso_id, codigo_etapa, area_usuaria)
    # ------------------------------------------------------------------
    op.execute(
        """
        DELETE FROM historial_cambios
        WHERE etapa_id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY proceso_id, codigo_etapa, area_usuaria
                           ORDER BY id DESC
                       ) AS rn
                FROM etapas_registro
                WHERE area_usuaria IS NOT NULL
            ) ranked
            WHERE rn > 1
        )
        """
    )
    op.execute(
        """
        DELETE FROM etapas_registro
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY proceso_id, codigo_etapa, area_usuaria
                           ORDER BY id DESC
                       ) AS rn
                FROM etapas_registro
                WHERE area_usuaria IS NOT NULL
            ) ranked
            WHERE rn > 1
        )
        """
    )

    # ------------------------------------------------------------------
    # 4. Eliminar duplicados de rondas de bucle
    #    Conservar solo MAX(id) por (proceso_id, codigo_etapa, nro_ronda)
    # ------------------------------------------------------------------
    op.execute(
        """
        DELETE FROM historial_cambios
        WHERE etapa_id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY proceso_id, codigo_etapa, nro_ronda
                           ORDER BY id DESC
                       ) AS rn
                FROM etapas_registro
                WHERE es_bucle = true
            ) ranked
            WHERE rn > 1
        )
        """
    )
    op.execute(
        """
        DELETE FROM etapas_registro
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY proceso_id, codigo_etapa, nro_ronda
                           ORDER BY id DESC
                       ) AS rn
                FROM etapas_registro
                WHERE es_bucle = true
            ) ranked
            WHERE rn > 1
        )
        """
    )

    # ------------------------------------------------------------------
    # 5. Crear índices únicos parciales
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE UNIQUE INDEX uq_etapa_simple_por_proceso
        ON etapas_registro (proceso_id, codigo_etapa)
        WHERE es_bucle = false AND area_usuaria IS NULL AND estado_etapa != 'OMITIDO'
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_etapa_por_area
        ON etapas_registro (proceso_id, codigo_etapa, area_usuaria)
        WHERE area_usuaria IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_ronda_bucle
        ON etapas_registro (proceso_id, codigo_etapa, nro_ronda)
        WHERE es_bucle = true
        """
    )


def downgrade() -> None:
    # Solo elimina los índices. Los datos borrados en upgrade NO se restauran.
    op.execute("DROP INDEX IF EXISTS uq_etapa_simple_por_proceso")
    op.execute("DROP INDEX IF EXISTS uq_etapa_por_area")
    op.execute("DROP INDEX IF EXISTS uq_ronda_bucle")
