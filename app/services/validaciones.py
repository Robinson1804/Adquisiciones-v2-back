"""Reglas de negocio centralizadas — C3b.

Todas las funciones son PURAS desde la perspectiva del llamador: leen estado de DB,
levantan HTTPException si la regla está bloqueada, o retornan None si la regla pasa.
Sin efectos secundarios (inserts/updates pertenecen a etapas_service).

Resolución spec vs diseño — códigos HTTP de bloqueo:
  - Spec §R1 sugiere 422; Design §tabla dice 409. Design es autoritativo para la
    implementación (cierra flags de la spec). Se usa 409 para todos los conflictos de
    estado (R1, R3, R5, R6, R7, prereq genérico, proceso CANCELADO gate).
  - 422 solo para R2 (motivo_cancel faltante = campo requerido inválido).
"""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso
from app.services.etapas_catalogo import ETAPAS_CATALOGO


# ---------------------------------------------------------------------------
# Gate: proceso activo (bloquea cualquier registro si CANCELADO)
# ---------------------------------------------------------------------------

def validar_proceso_activo(db: Session, proceso_id: int) -> None:
    """Bloquea el registro de nuevas etapas si el proceso está CANCELADO o CULMINADO."""
    proceso = db.get(Proceso, proceso_id)
    if proceso is None:
        return  # 404 se maneja en el router
    if proceso.estado == "CANCELADO":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Proceso cancelado. No se pueden agregar etapas.",
        )
    if proceso.estado == "CULMINADO":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Proceso culminado. No se pueden agregar etapas.",
        )


# ---------------------------------------------------------------------------
# R2 — E10: motivo_cancel requerido cuando resultado = SIN_PRESUPUESTO
# ---------------------------------------------------------------------------

def validar_r2_e10(db: Session, proceso_id: int, payload) -> None:
    """R2: Si resultado_eval='SIN_PRESUPUESTO', motivo_cancel es obligatorio.

    La transición de estado a CANCELADO la realiza etapas_service, no aquí.
    """
    if getattr(payload, "resultado_eval", None) == "SIN_PRESUPUESTO":
        if not getattr(payload, "motivo_cancel", None):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="motivo_cancel es requerido cuando resultado_eval = SIN_PRESUPUESTO",
            )


# ---------------------------------------------------------------------------
# R3 — E12: bloquear si alguna fila E11 está PENDIENTE
# ---------------------------------------------------------------------------

def validar_r3_e12(db: Session, proceso_id: int) -> None:
    """R3: E12 requiere que todas las filas E11 estén COMPLETADO."""
    areas_pendientes = db.execute(
        select(EtapaRegistro.area_usuaria).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E11",
            EtapaRegistro.estado_etapa == "PENDIENTE",
        )
    ).scalars().all()
    if areas_pendientes:
        areas = ", ".join(a for a in areas_pendientes if a)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No se puede consolidar E12: hay áreas con cert. presupuestal PENDIENTE"
                + (f": {areas}" if areas else "")
            ),
        )


# ---------------------------------------------------------------------------
# R4 — E16: alerta OTPP > 20 días (no bloquea, devuelve bool)
# ---------------------------------------------------------------------------

def calcular_alerta_r4_e16(etapa_row: EtapaRegistro, today: date | None = None) -> bool:
    """R4: True si la respuesta OTPP supera 20 días. No levanta excepción.

    Caso 1: fecha_resp_otpp - fecha_envio_otpp > 20.
    Caso 2: hoy - fecha_envio_otpp > 20 y fecha_resp_otpp es NULL.
    """
    if today is None:
        today = date.today()
    if etapa_row.fecha_envio_otpp is None:
        return False
    if etapa_row.fecha_resp_otpp is not None:
        return (etapa_row.fecha_resp_otpp - etapa_row.fecha_envio_otpp).days > 20
    return (today - etapa_row.fecha_envio_otpp).days > 20


# ---------------------------------------------------------------------------
# R5 — E25: bloquear si alguna fila E24 está PENDIENTE
# ---------------------------------------------------------------------------

def validar_r5_e25(db: Session, proceso_id: int) -> None:
    """R5: E25 requiere que todas las filas E24 estén COMPLETADO."""
    areas_pendientes = db.execute(
        select(EtapaRegistro.area_usuaria).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E24",
            EtapaRegistro.estado_etapa == "PENDIENTE",
        )
    ).scalars().all()
    if areas_pendientes:
        areas = ", ".join(a for a in areas_pendientes if a)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No conformidad final: áreas E24 pendientes"
                + (f": {areas}" if areas else "")
            ),
        )


# ---------------------------------------------------------------------------
# R6 — E05/E06 bucles: bloquear si E04 no está COMPLETADO
# ---------------------------------------------------------------------------

def validar_r6_bucle_tdr(db: Session, proceso_id: int) -> None:
    """R6: los bucles E05/E06 requieren que E04 esté COMPLETADO."""
    fila_e04 = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E04",
            EtapaRegistro.estado_etapa == "COMPLETADO",
        )
    ).scalars().first()
    if fila_e04 is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bucle TDR requiere E04 completado",
        )


# ---------------------------------------------------------------------------
# R7 — E09: bloquear si E08.resultado_eval != 'APROBADO'
# ---------------------------------------------------------------------------

def validar_r7_e09(db: Session, proceso_id: int) -> None:
    """R7: E09 requiere que E08 tenga resultado_eval = 'APROBADO'."""
    fila_e08 = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E08",
            EtapaRegistro.resultado_eval == "APROBADO",
        )
    ).scalars().first()
    if fila_e08 is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="E09 requiere evaluación técnica APROBADA (E08)",
        )


# ---------------------------------------------------------------------------
# R8 — E21: marca inicio del plazo (no bloquea, es un marcador)
# ---------------------------------------------------------------------------

def marcar_inicio_r8_e21(etapa_row: EtapaRegistro) -> None:
    """R8: E21.fecha_inicio es el inicio del plazo del servicio/bien.

    No bloquea. Esta función es un marcador semántico: la fecha ya se guarda
    en EtapaRegistro.fecha_inicio; aquí se documenta que ese campo ES el
    inicio del reloj del servicio (ver Design §R8).
    """
    # No-op: fecha_inicio ya persiste en etapa_row; el indicador se muestra en GET.
    return None


# ---------------------------------------------------------------------------
# Prerequisito genérico (Design D1)
# ---------------------------------------------------------------------------

def validar_prerequisito_generico(
    db: Session,
    proceso_id: int,
    cod: str,
) -> None:
    """Verifica que todos los prerequisitos del código estén COMPLETADO.

    Cubre el orden secuencial sin codificar 27 reglas a mano:
    E02←E01, E05/E06←E04, E09←E08, E12←E11, E25←E24.
    """
    spec = ETAPAS_CATALOGO.get(cod)
    if spec is None or not spec.prerequisitos:
        return

    for prereq_cod in spec.prerequisitos:
        prereq_spec = ETAPAS_CATALOGO.get(prereq_cod)
        if prereq_spec is None:
            continue

        rows = db.execute(
            select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso_id,
                EtapaRegistro.codigo_etapa == prereq_cod,
            )
        ).scalars().all()

        # NO_APLICA satisfies prerequisites — the stage was intentionally skipped.
        # OMITIDO is NOT equivalent (reserved for reinicio-TDR flow).
        _SATISFIES = {"COMPLETADO", "NO_APLICA"}

        if prereq_spec.por_area:
            # Todas las filas por área deben ser COMPLETADO o NO_APLICA
            if not rows or not all(r.estado_etapa in _SATISFIES for r in rows):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Prerequisito {prereq_cod} no completado para {cod}",
                )
        elif prereq_spec.es_bucle:
            # La última ronda debe ser COMPLETADO o NO_APLICA
            if not rows:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Prerequisito {prereq_cod} no completado para {cod}",
                )
            last = max(rows, key=lambda r: r.nro_ronda)
            if last.estado_etapa not in _SATISFIES:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Prerequisito {prereq_cod} no completado para {cod}",
                )
        else:
            # Etapa simple: debe existir una fila COMPLETADO o NO_APLICA
            satisfecha = any(r.estado_etapa in _SATISFIES for r in rows)
            if not satisfecha:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Prerequisito {prereq_cod} no completado para {cod}",
                )


# ---------------------------------------------------------------------------
# Reinicio TDR: validación de precondiciones
# ---------------------------------------------------------------------------

def validar_reinicio_tdr(db: Session, proceso_id: int) -> None:
    """Valida que el proceso pueda reiniciar TDR.

    Requiere:
    - proceso.estado == 'CANCELADO'
    - Existe al menos una fila E10 con resultado_eval == 'SIN_PRESUPUESTO'
    """
    proceso = db.get(Proceso, proceso_id)
    if proceso is None or proceso.estado != "CANCELADO":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Reinicio TDR solo disponible cuando el proceso fue cancelado "
                "por SIN_PRESUPUESTO en E10"
            ),
        )

    fila_e10 = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E10",
            EtapaRegistro.resultado_eval == "SIN_PRESUPUESTO",
        )
    ).scalars().first()

    if fila_e10 is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Reinicio TDR solo disponible cuando el proceso fue cancelado "
                "por SIN_PRESUPUESTO en E10"
            ),
        )
