"""Router for firma_secuencial endpoints — T07d.

Endpoints:
  POST  /procesos/{id}/firma-secuencial/{etapa_cod}  — create firma row
  PATCH /procesos/{id}/firma-secuencial/{firma_id}   — update firma estado
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user, require_role
from app.models.firma_secuencial import FirmaSecuencial
from app.models.usuario import Usuario
from app.routers.procesos import _get_active_proceso_or_404
from app.services.firma_secuencial_service import (
    actualizar_estado_firma,
    crear_firma,
)

router = APIRouter(prefix="/procesos", tags=["firma_secuencial"])


# ---------------------------------------------------------------------------
# Pydantic schemas (inline — small, local to this router)
# ---------------------------------------------------------------------------

class FirmaCreate(BaseModel):
    area: str
    orden: int
    ronda: int = 1


class FirmaUpdate(BaseModel):
    nuevo_estado: str
    motivo_rechazo: str | None = None


class FirmaOut(BaseModel):
    id: int
    proceso_id: int
    etapa_cod: str
    area: str
    orden: int
    estado: str
    ronda: int
    fecha_recibido: str | None = None
    fecha_firmado: str | None = None
    motivo_rechazo: str | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, f: FirmaSecuencial) -> "FirmaOut":
        return cls(
            id=f.id,
            proceso_id=f.proceso_id,
            etapa_cod=f.etapa_cod,
            area=f.area,
            orden=f.orden,
            estado=f.estado,
            ronda=f.ronda,
            fecha_recibido=str(f.fecha_recibido) if f.fecha_recibido else None,
            fecha_firmado=str(f.fecha_firmado) if f.fecha_firmado else None,
            motivo_rechazo=f.motivo_rechazo,
        )


# ---------------------------------------------------------------------------
# POST /procesos/{proceso_id}/firma-secuencial/{etapa_cod}
# ---------------------------------------------------------------------------

@router.post(
    "/{proceso_id}/firma-secuencial/{etapa_cod}",
    response_model=FirmaOut,
    status_code=status.HTTP_201_CREATED,
)
def create_firma(
    proceso_id: int,
    etapa_cod: str,
    body: FirmaCreate,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> FirmaOut:
    """Create a firma_secuencial row for a proceso+etapa+area combination."""
    _get_active_proceso_or_404(db, proceso_id)
    firma = crear_firma(
        db=db,
        proceso_id=proceso_id,
        etapa_cod=etapa_cod,
        area=body.area,
        orden=body.orden,
        ronda=body.ronda,
    )
    db.commit()
    db.refresh(firma)
    return FirmaOut.from_model(firma)


# ---------------------------------------------------------------------------
# PATCH /procesos/{proceso_id}/firma-secuencial/{firma_id}
# ---------------------------------------------------------------------------

@router.patch(
    "/{proceso_id}/firma-secuencial/{firma_id}",
    response_model=FirmaOut,
)
def update_firma(
    proceso_id: int,
    firma_id: int,
    body: FirmaUpdate,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> FirmaOut:
    """Update the estado of a firma_secuencial row."""
    _get_active_proceso_or_404(db, proceso_id)
    firma = actualizar_estado_firma(
        db=db,
        proceso_id=proceso_id,
        firma_id=firma_id,
        nuevo_estado=body.nuevo_estado,
        motivo_rechazo=body.motivo_rechazo,
    )
    db.commit()
    db.refresh(firma)
    return FirmaOut.from_model(firma)
