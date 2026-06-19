"""Tiempos router — cross-process timing heatmap matrix.

Endpoint: GET /tiempos/matriz
  - Optional filters: anno (int), estado (str), tipo (str), q (str)
  - Auth: any authenticated role (read-only)
  - N+1 prevention: loads ALL EtapaRegistro for matched procesos in one IN(...) query
  - Delegates aggregation to tiempos_matriz.construir_matriz (pure)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso
from app.models.usuario import Usuario
from app.schemas.tiempos_matriz import MatrizTiemposOut
from app.services.tiempos_matriz import construir_matriz

router = APIRouter(prefix="/tiempos", tags=["tiempos"])


@router.get("/matriz", response_model=MatrizTiemposOut)
def get_matriz_tiempos(
    anno: int | None = None,
    estado: str | None = None,
    tipo: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
) -> MatrizTiemposOut:
    """Return cross-process timing heatmap matrix.

    Filters mirror list_procesos (same fields, same comparisons, same null handling).
    q filters by id_proceso OR requerimiento (case-insensitive contains) — mirrors
    the `search` param in list_procesos exactly.
    Rows ordered by id_proceso ascending for a stable heatmap.
    N+1 avoided: one Proceso query + one batch EtapaRegistro IN(...) query.
    """
    # Build proceso query — mirrors list_procesos filter logic exactly
    stmt = select(Proceso).where(Proceso.eliminado_en.is_(None))
    if anno is not None:
        stmt = stmt.where(Proceso.anno == anno)
    if estado is not None:
        stmt = stmt.where(Proceso.estado == estado)
    if tipo is not None:
        stmt = stmt.where(Proceso.tipo == tipo)
    if q is not None:
        pattern = f"%{q}%"
        stmt = stmt.where(
            Proceso.requerimiento.ilike(pattern) | Proceso.id_proceso.ilike(pattern)
        )

    # Order by id_proceso asc for stable heatmap rows
    stmt = stmt.order_by(Proceso.id_proceso.asc())

    procesos = list(db.execute(stmt).scalars().all())

    if not procesos:
        return construir_matriz([], [])

    # Batch load all EtapaRegistro rows for the matched procesos — ONE query
    proceso_ids = [p.id for p in procesos]
    etapas_rows = list(
        db.execute(
            select(EtapaRegistro).where(EtapaRegistro.proceso_id.in_(proceso_ids))
        ).scalars().all()
    )

    return construir_matriz(procesos, etapas_rows)
