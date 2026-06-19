"""Etapas router — endpoints for stage registration, updates, loops, and TDR restart.

C3a: mechanics. C3b: R1-R8 validation wired in services; reiniciar-tdr added.

Endpoints:
  GET  /procesos/{id}/etapas               → grouped timeline + progreso
  POST /procesos/{id}/etapas               → register a stage row (R1-R8 enforced)
  PUT  /etapas/{id}                        → update a stage row (with audit)
  POST /procesos/{id}/etapas/{cod}/bucle   → add a loop round (R6 enforced)
  POST /procesos/{id}/reiniciar-tdr        → restart TDR from E02 (ADMIN/EDITOR)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user, require_role
from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso
from app.models.usuario import Usuario
from app.schemas.etapa import (
    BucleCreate,
    EtapaCreate,
    EtapaOut,
    EtapasResponseOut,
    EtapaUpdate,
)
from app.services.etapas_catalogo import ETAPAS_CATALOGO
from app.services.etapas_service import (
    actualizar_etapa,
    agregar_ronda_bucle,
    agrupar_etapas,
    calcular_progreso,
    registrar_etapa,
    reiniciar_tdr,
)

router = APIRouter(tags=["etapas"])

_BUCLE_CODS: frozenset[str] = frozenset(
    cod for cod, spec in ETAPAS_CATALOGO.items() if spec.es_bucle
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_active_proceso_or_404(db: Session, proceso_id: int) -> Proceso:
    proceso = db.get(Proceso, proceso_id)
    if proceso is None or proceso.eliminado_en is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proceso {proceso_id} no encontrado",
        )
    return proceso


def _get_etapa_or_404(db: Session, etapa_id: int) -> EtapaRegistro:
    etapa = db.get(EtapaRegistro, etapa_id)
    if etapa is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Etapa {etapa_id} no encontrada",
        )
    return etapa


def _etapa_to_out(etapa: EtapaRegistro) -> EtapaOut:
    """Convert EtapaRegistro ORM to EtapaOut, computing vencimiento_ocs."""
    from datetime import timedelta
    vencimiento_ocs = None
    if (
        etapa.codigo_etapa == "E19"
        and etapa.fecha_inicio
        and etapa.plazo_entrega is not None
    ):
        vencimiento_ocs = etapa.fecha_inicio + timedelta(days=etapa.plazo_entrega)

    out = EtapaOut.model_validate(etapa)
    out.vencimiento_ocs = vencimiento_ocs
    return out


# ---------------------------------------------------------------------------
# GET /procesos/{proceso_id}/etapas
# ---------------------------------------------------------------------------

@router.get(
    "/procesos/{proceso_id}/etapas",
    response_model=EtapasResponseOut,
)
def get_etapas(
    proceso_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
) -> EtapasResponseOut:
    """Return all 27 stages grouped + progreso (all PENDIENTE if no rows yet)."""
    proceso = _get_active_proceso_or_404(db, proceso_id)

    rows = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id
        )
    ).scalars().all()

    etapas_agrupadas = agrupar_etapas(
        list(rows),
        areas_usuarias=proceso.areas_usuarias,
    )
    progreso = calcular_progreso(list(rows))

    # CULMINADO override: process is fully finished regardless of optional
    # loop stages (E05/E06) that may never have been completed.
    if proceso.estado == "CULMINADO":
        progreso = progreso.model_copy(
            update={"porcentaje": 100.0, "etapa_actual": None}
        )

    return EtapasResponseOut(etapas=etapas_agrupadas, progreso=progreso)


# ---------------------------------------------------------------------------
# POST /procesos/{proceso_id}/etapas
# ---------------------------------------------------------------------------

@router.post(
    "/procesos/{proceso_id}/etapas",
    response_model=EtapaOut,
    status_code=status.HTTP_201_CREATED,
)
def post_etapa(
    proceso_id: int,
    body: EtapaCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> EtapaOut:
    """Register a new stage row for the proceso."""
    _get_active_proceso_or_404(db, proceso_id)
    # Validaciones R1-R8 are invoked inside registrar_etapa (C3b wired)
    etapa = registrar_etapa(db, proceso_id, body, current_user.username)
    db.commit()
    db.refresh(etapa)
    return _etapa_to_out(etapa)


# ---------------------------------------------------------------------------
# PUT /etapas/{etapa_id}
# ---------------------------------------------------------------------------

@router.put(
    "/etapas/{etapa_id}",
    response_model=EtapaOut,
)
def put_etapa(
    etapa_id: int,
    body: EtapaUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> EtapaOut:
    """Update an existing stage row. Generates historial_cambios entries."""
    etapa = _get_etapa_or_404(db, etapa_id)
    etapa = actualizar_etapa(db, etapa, body, current_user.username)
    db.commit()
    db.refresh(etapa)
    return _etapa_to_out(etapa)


# ---------------------------------------------------------------------------
# POST /procesos/{proceso_id}/etapas/{cod}/bucle
# ---------------------------------------------------------------------------

@router.post(
    "/procesos/{proceso_id}/etapas/{cod}/bucle",
    response_model=EtapaOut,
    status_code=status.HTTP_201_CREATED,
)
def post_bucle(
    proceso_id: int,
    cod: str,
    body: BucleCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> EtapaOut:
    """Add a new round for a loop-type stage (E05, E06, E08a, E08b)."""
    if cod not in _BUCLE_CODS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El código '{cod}' no es una etapa de bucle. "
                   f"Etapas de bucle válidas: {sorted(_BUCLE_CODS)}",
        )
    _get_active_proceso_or_404(db, proceso_id)
    # R6 validation is invoked inside agregar_ronda_bucle (C3b wired)
    etapa = agregar_ronda_bucle(db, proceso_id, cod, body, current_user.username)
    db.commit()
    db.refresh(etapa)
    return _etapa_to_out(etapa)


# ---------------------------------------------------------------------------
# POST /procesos/{proceso_id}/reiniciar-tdr  (C3b — Design D3)
# ---------------------------------------------------------------------------

@router.post(
    "/procesos/{proceso_id}/reiniciar-tdr",
    response_model=EtapaOut,
    status_code=status.HTTP_200_OK,
)
def post_reiniciar_tdr(
    proceso_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> EtapaOut:
    """Restart TDR from E02 on a CANCELADO proceso (E10 SIN_PRESUPUESTO only).

    Marks all E02-E09 rows as OMITIDO (preserves audit), inserts a fresh
    E02 PENDIENTE, restores proceso.estado = 'EN PROCESO'.
    ADMIN/EDITOR only. Returns the newly inserted E02 row.
    """
    _get_active_proceso_or_404(db, proceso_id)
    nueva_e02 = reiniciar_tdr(db, proceso_id, current_user.username)
    db.commit()
    db.refresh(nueva_e02)
    return _etapa_to_out(nueva_e02)
