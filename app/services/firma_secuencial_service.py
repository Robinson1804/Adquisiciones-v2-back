"""firma_secuencial_service — T07b.

Service layer for the firma_secuencial table.
Implements sequential signing logic: areas sign in order (orden field);
an area with orden=N cannot be set to FIRMADO if any area with orden<N
is still PENDIENTE or RECIBIDO.
"""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.etapa import EtapaRegistro
from app.models.firma_secuencial import FirmaSecuencial

# Valid transitions per estado
_VALID_ESTADOS = frozenset({"PENDIENTE", "RECIBIDO", "FIRMADO", "RECHAZADO"})


def crear_firma(
    db: Session,
    proceso_id: int,
    etapa_cod: str,
    area: str,
    orden: int,
    ronda: int = 1,
) -> FirmaSecuencial:
    """Create a new firma_secuencial row in PENDIENTE state.

    Raises 409 if (proceso_id, etapa_cod, area, ronda) already exists.
    """
    row = FirmaSecuencial(
        proceso_id=proceso_id,
        etapa_cod=etapa_cod,
        area=area,
        orden=orden,
        ronda=ronda,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Firma para área '{area}' en etapa '{etapa_cod}' ronda {ronda} ya existe."
            ),
        )
    db.refresh(row)
    return row


def actualizar_estado_firma(
    db: Session,
    proceso_id: int,
    firma_id: int,
    nuevo_estado: str,
    motivo_rechazo: str | None = None,
) -> FirmaSecuencial:
    """Update the estado of a firma_secuencial row.

    Business rule: an area cannot be set to FIRMADO if any area with a
    lower orden (for the same proceso_id, etapa_cod, ronda) is still in
    PENDIENTE or RECIBIDO.

    Raises:
        404 — firma not found or belongs to a different proceso.
        422 — orden constraint violated (cannot sign before lower-order areas).
        422 — invalid estado value.
    """
    if nuevo_estado not in _VALID_ESTADOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Estado inválido: '{nuevo_estado}'. Válidos: {sorted(_VALID_ESTADOS)}",
        )

    firma = db.execute(
        select(FirmaSecuencial).where(
            FirmaSecuencial.id == firma_id,
            FirmaSecuencial.proceso_id == proceso_id,
        )
    ).scalar_one_or_none()

    if firma is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firma {firma_id} no encontrada para proceso {proceso_id}.",
        )

    # Enforce sequential signing: only FIRMADO transition requires the check
    if nuevo_estado == "FIRMADO":
        blocking_areas = db.execute(
            select(FirmaSecuencial.area).where(
                FirmaSecuencial.proceso_id == proceso_id,
                FirmaSecuencial.etapa_cod == firma.etapa_cod,
                FirmaSecuencial.ronda == firma.ronda,
                FirmaSecuencial.orden < firma.orden,
                FirmaSecuencial.estado.in_(["PENDIENTE", "RECIBIDO"]),
            )
        ).scalars().all()
        if blocking_areas:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"No se puede marcar FIRMADO: áreas con orden menor aún pendientes: "
                    f"{', '.join(blocking_areas)}"
                ),
            )

    firma.estado = nuevo_estado
    if nuevo_estado == "RECIBIDO":
        firma.fecha_recibido = date.today()
    elif nuevo_estado == "FIRMADO":
        firma.fecha_firmado = date.today()
    elif nuevo_estado == "RECHAZADO":
        firma.motivo_rechazo = motivo_rechazo

    db.flush()
    db.refresh(firma)

    # Auto-complete etapas_registro when all firmas for this (proceso, etapa, ronda) are FIRMADO
    if nuevo_estado == "FIRMADO":
        _check_all_firmado_and_complete(
            db,
            proceso_id=proceso_id,
            etapa_cod=firma.etapa_cod,
            ronda=firma.ronda,
        )

    return firma


def _check_all_firmado_and_complete(
    db: Session,
    proceso_id: int,
    etapa_cod: str,
    ronda: int,
) -> None:
    """If ALL firma_secuencial rows for (proceso, etapa, ronda) are FIRMADO,
    upsert the corresponding etapas_registro row to COMPLETADO.

    Called within the same transaction as actualizar_estado_firma so the
    etapas_registro update is atomic with the firma state change.

    Behaviour per etapa type:
    - E02b (not es_bucle): matches on proceso_id + codigo_etapa only
    - E06c (es_bucle): matches on proceso_id + codigo_etapa + nro_ronda
    """
    total_count: int = db.execute(
        select(func.count()).where(
            FirmaSecuencial.proceso_id == proceso_id,
            FirmaSecuencial.etapa_cod == etapa_cod,
            FirmaSecuencial.ronda == ronda,
        )
    ).scalar_one()

    if total_count == 0:
        return

    firmado_count: int = db.execute(
        select(func.count()).where(
            FirmaSecuencial.proceso_id == proceso_id,
            FirmaSecuencial.etapa_cod == etapa_cod,
            FirmaSecuencial.ronda == ronda,
            FirmaSecuencial.estado == "FIRMADO",
        )
    ).scalar_one()

    if firmado_count < total_count:
        return

    # All rows are FIRMADO — upsert etapas_registro to COMPLETADO.
    from app.services.etapas_catalogo import ETAPAS_CATALOGO

    spec = ETAPAS_CATALOGO.get(etapa_cod)
    es_bucle = spec.es_bucle if spec else False

    if es_bucle:
        # Match by ronda (nro_ronda column in etapas_registro)
        fila = db.execute(
            select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso_id,
                EtapaRegistro.codigo_etapa == etapa_cod,
                EtapaRegistro.nro_ronda == ronda,
            )
        ).scalars().first()
    else:
        fila = db.execute(
            select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso_id,
                EtapaRegistro.codigo_etapa == etapa_cod,
            )
        ).scalars().first()

    if fila is not None:
        fila.estado_etapa = "COMPLETADO"
        fila.fecha_fin = date.today()
    else:
        from app.services.etapas_catalogo import ETAPAS_CATALOGO

        nombre = spec.nombre if spec else etapa_cod
        area_responsable = spec.area_responsable if spec else None
        new_row = EtapaRegistro(
            proceso_id=proceso_id,
            codigo_etapa=etapa_cod,
            nombre_etapa=nombre,
            area_responsable=area_responsable,
            estado_etapa="COMPLETADO",
            fecha_fin=date.today(),
            es_bucle=es_bucle,
            nro_ronda=ronda,
            registrado_por="sistema",
        )
        db.add(new_row)

    db.flush()
