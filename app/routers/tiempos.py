"""Read-only endpoints for timing analysis."""
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
    """Return the cross-process timing matrix with optional filters."""
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

    procesos = list(db.execute(stmt.order_by(Proceso.id_proceso.asc())).scalars().all())
    if not procesos:
        return construir_matriz([], [])

    proceso_ids = [proceso.id for proceso in procesos]
    etapas_rows = list(
        db.execute(
            select(EtapaRegistro).where(EtapaRegistro.proceso_id.in_(proceso_ids))
        ).scalars().all()
    )

    return construir_matriz(procesos, etapas_rows)
