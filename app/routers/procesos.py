"""Procesos router — CRUD endpoints for the procesos resource.

Design decisions implemented here:
  D1 — soft delete via eliminado_en TIMESTAMP NULL
  D2 — id_proceso generation: pg_advisory_xact_lock + MAX+1 + retry
  D3 — CMN captured as E01 rows in etapas_registro at creation time
  D4 — historial_cambios deferred to C3 (no audit writes in C2)

Service layer is inline (helpers prefixed _) per design §1 decision.
"""
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user, require_role
from app.models.etapa import EtapaRegistro
from app.models.montos import MontosProceso
from app.models.proceso import Proceso
from app.models.usuario import Usuario
from app.schemas.etapa import EtapaOut, TiemposProcesoOut
from app.schemas.proceso import (
    CmnPorArea,
    MontosOut,
    OrdenServicioIn,
    PaginatedProcesos,
    ProcesoCreate,
    ProcesoOut,
    ProcesoUpdate,
)

router = APIRouter(prefix="/procesos", tags=["procesos"])

# ---------------------------------------------------------------------------
# Private helpers (inline service layer — C2 design decision)
# ---------------------------------------------------------------------------

_E01_NOMBRE = "Solicitud de requerimiento TIC (Áreas → OTIN)"  # legacy — kept for migration refs
_E01A_NOMBRE = "Solicitud inicial área iniciadora (Área → OTIN)"
_MAX_ID_RETRIES = 3


def _generar_id_proceso(db: Session, anno: int) -> str:
    """Generate the next sequential id_proceso for the given year.

    Uses pg_advisory_xact_lock to serialize generation within the SAME
    transaction as the INSERT (autocommit=False guaranteed by SessionLocal).
    The UNIQUE constraint on id_proceso is the final safety net.
    Counts ALL rows for the year including soft-deleted ones — never recycles.
    """
    lock_key = f"proceso_seq_{anno}"
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": lock_key},
    )
    next_n = db.execute(
        text(
            "SELECT COALESCE(MAX(CAST(SPLIT_PART(id_proceso,'-',2) AS INTEGER)), 0) + 1 "
            "FROM procesos WHERE anno = :anno"
        ),
        {"anno": anno},
    ).scalar_one()
    return f"{anno}-{next_n:03d}"


def _crear_etapas_e01(
    db: Session,
    proceso_id: int,
    areas: list[str],
    cmn_por_area: list[CmnPorArea],
    usuario: str,
    fecha_solicitud: date | None = None,
    area_iniciadora: str | None = None,
) -> None:
    """flujo-real-otin-v2: Auto-create E01a (single, non-por-area) when fecha_solicitud
    is provided. The old per-area E01 pattern is superseded; E01c rows are registered
    manually via POST /procesos/{id}/etapas.

    When fecha_solicitud is provided and area_iniciadora is set, E01a is auto-completed
    (COMPLETADO) — it is the kickoff/anchor of the timeline.
    When fecha_solicitud is provided without area_iniciadora, E01a is still created
    as COMPLETADO (using the first area as a fallback context).
    """
    if fecha_solicitud is None:
        # No auto-creation without a kickoff date — registro manual via API
        return

    db.add(
        EtapaRegistro(
            proceso_id=proceso_id,
            codigo_etapa="E01a",
            nombre_etapa=_E01A_NOMBRE,
            area_responsable="AREAS",
            area_usuaria=None,  # E01a is NOT por-area
            fecha_inicio=fecha_solicitud,
            estado_etapa="COMPLETADO",
            registrado_por=usuario,
            nro_ronda=1,
        )
    )


def _get_active_proceso_or_404(db: Session, proceso_id: int) -> Proceso:
    """Return active (non-deleted) Proceso or raise 404."""
    proceso = db.get(Proceso, proceso_id)
    if proceso is None or proceso.eliminado_en is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proceso {proceso_id} no encontrado",
        )
    return proceso


# ---------------------------------------------------------------------------
# Estado transition validation
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "EN PROCESO": {"CULMINADO", "CANCELADO"},
    "CULMINADO": set(),
    "CANCELADO": set(),
}


def _validate_estado_transition(current: str, nuevo: str) -> None:
    allowed = _VALID_TRANSITIONS.get(current, set())
    if nuevo not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Transición de estado inválida: '{current}' → '{nuevo}'. "
                f"Transiciones permitidas: {sorted(allowed) or 'ninguna'}."
            ),
        )


# ---------------------------------------------------------------------------
# GET /procesos — paginated list (excludes soft-deleted)
# ---------------------------------------------------------------------------

@router.get("", response_model=PaginatedProcesos)
def list_procesos(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    anno: int | None = None,
    estado: str | None = None,
    tipo: str | None = None,
    search: str | None = None,
    area: str | None = None,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
) -> PaginatedProcesos:
    base = select(Proceso).where(Proceso.eliminado_en.is_(None))

    if anno is not None:
        base = base.where(Proceso.anno == anno)
    if estado is not None:
        base = base.where(Proceso.estado == estado)
    if tipo is not None:
        base = base.where(Proceso.tipo == tipo)
    if search is not None:
        pattern = f"%{search}%"
        base = base.where(
            Proceso.requerimiento.ilike(pattern) | Proceso.id_proceso.ilike(pattern)
        )
    if area is not None:
        base = base.where(Proceso.areas_usuarias.any(area))

    total: int = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    rows = db.execute(
        base.order_by(Proceso.fecha_creacion.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    return PaginatedProcesos.build(
        items=[ProcesoOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# POST /procesos — create (ADMIN or EDITOR)
# ---------------------------------------------------------------------------

@router.post("", response_model=ProcesoOut, status_code=status.HTTP_201_CREATED)
def create_proceso(
    body: ProcesoCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> ProcesoOut:
    for attempt in range(_MAX_ID_RETRIES):
        try:
            id_proceso = _generar_id_proceso(db, body.anno)
            proceso = Proceso(
                id_proceso=id_proceso,
                requerimiento=body.requerimiento,
                tipo=body.tipo,
                unidad_resp="OTIN",  # always hardcoded — ignores body.unidad_resp
                areas_usuarias=body.areas_usuarias,
                pim=body.pim,
                anno=body.anno,
                estado="EN PROCESO",
                creado_por=current_user.username,
                denominacion_cmn=body.denominacion_cmn,
                clasificador_cmn=body.clasificador_cmn,
                area_iniciadora=body.area_iniciadora,
            )
            db.add(proceso)
            db.flush()  # assigns proceso.id without committing
            _crear_etapas_e01(
                db,
                proceso.id,
                body.areas_usuarias,
                body.cmn_por_area,
                current_user.username,
                body.fecha_solicitud,
                body.area_iniciadora,
            )
            db.commit()
            db.refresh(proceso)
            return ProcesoOut.model_validate(proceso)
        except IntegrityError:
            db.rollback()
            if attempt == _MAX_ID_RETRIES - 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="No se pudo generar un id_proceso único. Intente de nuevo.",
                )

    # Unreachable but satisfies type checkers
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno")


# ---------------------------------------------------------------------------
# GET /procesos/{id} — detail (any authenticated user)
# ---------------------------------------------------------------------------

@router.get("/{proceso_id}", response_model=ProcesoOut)
def get_proceso(
    proceso_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
) -> ProcesoOut:
    from app.services.inferencia_etapas import derivar_tiempos

    proceso = _get_active_proceso_or_404(db, proceso_id)
    rows = db.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proceso_id)
    ).scalars().all()

    fi, fo, dt, etapa_actual = derivar_tiempos(rows, proceso)

    out = ProcesoOut.model_validate(proceso)
    out.fecha_indagacion = fi
    out.fecha_transicion_ota = fo
    out.dias_transcurridos = dt
    out.etapa_actual_avance = etapa_actual
    return out


# ---------------------------------------------------------------------------
# GET /procesos/{id}/montos — montos consolidados para la ficha S4 (C3b)
# ---------------------------------------------------------------------------

@router.get("/{proceso_id}/montos", response_model=MontosOut | None)
def get_montos_proceso(
    proceso_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
) -> MontosOut | None:
    _get_active_proceso_or_404(db, proceso_id)
    montos = db.execute(
        select(MontosProceso).where(MontosProceso.proceso_id == proceso_id)
    ).scalar_one_or_none()
    return MontosOut.model_validate(montos) if montos else None


# ---------------------------------------------------------------------------
# PUT /procesos/{id} — update (ADMIN or EDITOR)
# ---------------------------------------------------------------------------

@router.put("/{proceso_id}", response_model=ProcesoOut)
def update_proceso(
    proceso_id: int,
    body: ProcesoUpdate,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> ProcesoOut:
    proceso = _get_active_proceso_or_404(db, proceso_id)

    # Validate estado transition first (before any mutation)
    if body.estado is not None and body.estado != proceso.estado:
        _validate_estado_transition(proceso.estado, body.estado)

    # Enforce motivo_cancel when transitioning to CANCELADO
    nuevo_estado = body.estado if body.estado is not None else proceso.estado
    if nuevo_estado == "CANCELADO" and not (body.motivo_cancel or proceso.motivo_cancel):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="motivo_cancel es requerido al cancelar un proceso.",
        )

    # Apply non-None fields
    _MUTABLE_FIELDS = (
        "requerimiento", "tipo", "unidad_resp", "areas_usuarias", "pim",
        "estado", "motivo_cancel",
        # flujo-real-otin-v2 — campos editables de Ficha
        "anno", "area_iniciadora", "denominacion_cmn", "clasificador_cmn",
    )
    for field in _MUTABLE_FIELDS:
        value = getattr(body, field)
        if value is not None:
            setattr(proceso, field, value)

    db.commit()
    db.refresh(proceso)
    return ProcesoOut.model_validate(proceso)


# ---------------------------------------------------------------------------
# DELETE /procesos/{id} — soft delete (ADMIN or EDITOR)
# ---------------------------------------------------------------------------

@router.delete("/{proceso_id}")
def delete_proceso(
    proceso_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> dict:
    proceso = _get_active_proceso_or_404(db, proceso_id)
    proceso.eliminado_en = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"message": "Proceso eliminado", "id": proceso_id}


# ---------------------------------------------------------------------------
# GET /procesos/{id}/tiempos — lead-time / cuello de botella (control-tiempos)
# ---------------------------------------------------------------------------

@router.get("/{proceso_id}/tiempos", response_model=TiemposProcesoOut)
def get_tiempos_proceso(
    proceso_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
) -> TiemposProcesoOut:
    """Return interval chain, total days, per-area breakdown and bottleneck.

    Walks CADENA (26 non-bucle milestones) and computes consecutive intervals
    between dated stages.  Returns empty sets when fewer than 2 milestones are
    dated.  Auth: any authenticated role (read-only).
    """
    from app.services.etapas_service import agrupar_etapas
    from app.services.tiempos_service import calcular_tiempos

    _get_active_proceso_or_404(db, proceso_id)

    rows = db.execute(
        select(EtapaRegistro).where(EtapaRegistro.proceso_id == proceso_id)
    ).scalars().all()

    agrupadas = agrupar_etapas(list(rows))
    return calcular_tiempos(agrupadas)


# ---------------------------------------------------------------------------
# POST /procesos/{id}/registrar-orden-servicio — wizard batch O/S (T08b)
# ---------------------------------------------------------------------------

#: Stages created by the O/S wizard (E14-E20 in catalog order)
_OS_ETAPAS: tuple[str, ...] = ("E14", "E15", "E16", "E17", "E18", "E19", "E20")


@router.post(
    "/{proceso_id}/registrar-orden-servicio",
    response_model=list[EtapaOut],
    status_code=status.HTTP_201_CREATED,
)
def registrar_orden_servicio(
    proceso_id: int,
    body: OrdenServicioIn,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> list[EtapaOut]:
    """Wizard: batch-register E14-E20 in a single transaction.

    Preconditions:
    - E13 must be COMPLETADO (or NO_APLICA) → 422 otherwise.
    - E19 must not already exist → 409 otherwise.
    All stages use body.fecha_os as fecha_inicio unless overridden via body.fechas_estimadas.
    """
    from app.models.etapa import EtapaRegistro
    from app.services.etapas_catalogo import ETAPAS_CATALOGO

    _get_active_proceso_or_404(db, proceso_id)

    # Precondition: E13 COMPLETADO or NO_APLICA
    e13 = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E13",
            EtapaRegistro.estado_etapa.in_(["COMPLETADO", "NO_APLICA"]),
        )
    ).scalar_one_or_none()
    if e13 is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="E13 debe estar COMPLETADO o NO_APLICA antes de registrar la O/S.",
        )

    # Precondition: E19 must not exist
    e19_exists = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E19",
        )
    ).scalar_one_or_none()
    if e19_exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="E19 ya existe — Orden de Servicio ya registrada para este proceso.",
        )

    fechas = body.fechas_estimadas or {}
    created: list[EtapaRegistro] = []
    for cod in _OS_ETAPAS:
        spec = ETAPAS_CATALOGO.get(cod)
        fecha = fechas.get(cod, body.fecha_os)
        row = EtapaRegistro(
            proceso_id=proceso_id,
            codigo_etapa=cod,
            nombre_etapa=spec.nombre if spec else f"Etapa {cod}",
            area_responsable=spec.area_responsable if spec else "OTIN",
            es_bucle=False,
            nro_ronda=1,
            estado_etapa="COMPLETADO",
            fecha_inicio=fecha,
            observaciones=body.observaciones,
            registrado_por=current_user.username,
        )
        db.add(row)
        created.append(row)

    db.flush()
    db.commit()
    for row in created:
        db.refresh(row)

    return [EtapaOut.model_validate(row) for row in created]
