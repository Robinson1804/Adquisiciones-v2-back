"""Service layer for ingesta de correos documental.

Implementa la lógica de negocio del pipeline de ingesta:
  - ingestar_correo: idempotencia por entry_id + auto-vinculación (ADR-5)
  - vincular: rutina común AUTO + manual (mueve adjuntos, ancla oficio, audit)
  - aprobar_correo: camino manual, delega en vincular
  - rechazar_correo: marca RECHAZADO + motivo + audit
  - desvincular_correo: reversa (docs definitivo→staging, estado→PENDIENTE, audit)
  - get_pendientes / get_pendientes_entry_ids: bandeja de revisión
  - corregir_correo: PATCH parcial de campos editables (solo PENDIENTE)
  - get_documentos_proceso: índice documental de un proceso

Patrón: NO importa de tools/ (tools/ es local, no se despliega en Railway).
Normalizers disponibles en app/services/ingesta_normalizers.py.
"""
from __future__ import annotations

import base64
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.historial import HistorialCambio
from app.models.ingesta import IngestaCorreo, IngestaDocumento
from app.models.proceso import Proceso
from app.schemas.ingesta import (
    CorreoCorreccionIn,
    CorreoIngestaIn,
    CorreoPropuestaIn,
    IngestaCorreoResultOut,
)
from app.services.archivos_service import (
    ALLOWED_CONTENT_TYPES,
    assert_within_upload_dir,
    sanitize_filename,
)
from app.services.ingesta_normalizers import (
    AUTO_LINK_THRESHOLD,
    evaluar_similitud_nombre,
    normalizar_nombre_servicio,
    normalizar_oficio,
)

# Tamaño máximo de adjunto (reutiliza settings.MAX_UPLOAD_BYTES)
_MAX_BYTES = settings.MAX_UPLOAD_BYTES


# ---------------------------------------------------------------------------
# Helpers de almacenamiento
# ---------------------------------------------------------------------------

def _staging_dir() -> Path:
    """Directorio de staging para adjuntos no vinculados aún."""
    d = settings.upload_path / "ingesta_staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _proc_dir(proceso_id: int) -> Path:
    """Directorio definitivo para un proceso."""
    d = settings.upload_path / f"proc_{proceso_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_safe_ext_ingesta(content_type: str) -> str:
    """Extensión segura para el content_type. Reutiliza ALLOWED_CONTENT_TYPES.

    Para ingesta aceptamos también image/gif, image/tiff, image/webp
    que no están en el whitelist de etapa_archivos. Los mapeamos a extensiones seguras.
    """
    extra_map: dict[str, str] = {
        "image/gif": ".gif",
        "image/tiff": ".tif",
        "image/webp": ".webp",
        "application/zip": ".zip",
        "application/x-zip-compressed": ".zip",
        "application/vnd.rar": ".rar",
        "application/x-rar-compressed": ".rar",
        "application/x-7z-compressed": ".7z",
    }
    ext = ALLOWED_CONTENT_TYPES.get(content_type) or extra_map.get(content_type)
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Tipo de archivo no permitido: '{content_type}'.",
        )
    return ext


def _save_b64_to_path(contenido_b64: str, dest: Path) -> None:
    """Decodifica base64 y escribe en dest. Raises 422 si base64 inválido."""
    try:
        data = base64.b64decode(contenido_b64)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Contenido base64 inválido.",
        )
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Adjunto demasiado grande: {len(data)} bytes. "
                f"Máximo: {_MAX_BYTES} bytes."
            ),
        )
    dest.write_bytes(data)


# ---------------------------------------------------------------------------
# Core: ingestar_correo
# ---------------------------------------------------------------------------

def ingestar_correo(db: Session, payload: CorreoIngestaIn) -> IngestaCorreoResultOut:
    """Persiste un correo extraído en staging y evalúa auto-vinculación (ADR-5).

    Idempotente por entry_id: si ya existe devuelve el registro existente
    con status='already_ingested' sin modificar nada.

    Flujo:
    1. Check de existencia por entry_id.
    2. Normalizar oficio.
    3. Guardar adjuntos en staging (write-disk-first).
    4. INSERT IngestaCorreo + IngestaDocumento.
    5. Evaluar auto-vinculación.
    6. Commit.
    """
    # 1. Idempotencia: check previo (evita IntegrityError en la mayoría de casos)
    existing = db.execute(
        select(IngestaCorreo).where(IngestaCorreo.entry_id == payload.entry_id)
    ).scalar_one_or_none()
    if existing is not None:
        return IngestaCorreoResultOut(
            id=existing.id,
            estado_revision=existing.estado_revision,  # type: ignore[arg-type]
            proceso_id=existing.proceso_id,
            match_confianza=float(existing.match_confianza) if existing.match_confianza is not None else None,
            status="already_ingested",
        )

    # 2. Normalizar oficio (el payload puede traerlo ya normalizado, pero re-normalizamos)
    numero_oficio_norm = normalizar_oficio(payload.numero_oficio_raw) if payload.numero_oficio_raw else payload.numero_oficio

    # 3. Guardar adjuntos en staging (write-disk-first; rollback en fallo)
    staging = _staging_dir()
    doc_rows: list[IngestaDocumento] = []
    doc_paths: list[Path] = []  # para cleanup si falla el commit

    for doc in payload.documentos:
        ext = _get_safe_ext_ingesta(doc.content_type)
        nombre_almacenado = uuid.uuid4().hex + ext
        dest = staging / nombre_almacenado
        assert_within_upload_dir(dest, settings.upload_path)

        _save_b64_to_path(doc.contenido_b64, dest)
        doc_paths.append(dest)

        ruta_relativa = str(Path("ingesta_staging") / nombre_almacenado)
        doc_row = IngestaDocumento(
            nombre_original=sanitize_filename(doc.nombre_original),
            nombre_almacenado=nombre_almacenado,
            ruta_relativa=ruta_relativa,
            content_type=doc.content_type,
            tamano_bytes=doc.tamano_bytes,
            tipo_clasificado=doc.tipo_clasificado,
            confianza=doc.confianza,
        )
        doc_rows.append(doc_row)

    # 4. INSERT IngestaCorreo
    correo = IngestaCorreo(
        entry_id=payload.entry_id,
        subject=payload.subject,
        sender_name=payload.sender_name,
        sender_email=payload.sender_email,
        received_at=payload.received_at,
        body_clean=payload.body_clean,
        nombre_servicio=payload.nombre_servicio,
        nombre_servicio_normalizado=payload.nombre_servicio_normalizado,
        numero_oficio_raw=payload.numero_oficio_raw,
        numero_oficio=numero_oficio_norm,
        oss=payload.oss or None,
        siaf=payload.siaf,
        proveedor=payload.proveedor,
        tipo=payload.tipo,
        fecha_documento=payload.fecha_documento,
        fecha_recepcion=payload.fecha_recepcion,
        relevancia_score=payload.relevancia_score,
        relevancia_motivos=payload.relevancia_motivos,
        proceso_sugerido_id=payload.proceso_sugerido_id,
        etapa_sugerida=payload.etapa_sugerida,
        fase_sugerida=payload.fase_sugerida,
        resumen_sugerido=payload.resumen_sugerido,
        estado_revision="PENDIENTE",
    )

    try:
        db.add(correo)
        db.flush()  # obtiene correo.id

        # Asociar documentos al correo
        for doc_row in doc_rows:
            doc_row.ingesta_correo_id = correo.id
            db.add(doc_row)
        db.flush()

    except IntegrityError:
        # Race condition: otro proceso insertó el mismo entry_id concurrentemente
        db.rollback()
        for p in doc_paths:
            p.unlink(missing_ok=True)
        existing = db.execute(
            select(IngestaCorreo).where(IngestaCorreo.entry_id == payload.entry_id)
        ).scalar_one()
        return IngestaCorreoResultOut(
            id=existing.id,
            estado_revision=existing.estado_revision,  # type: ignore[arg-type]
            proceso_id=existing.proceso_id,
            match_confianza=float(existing.match_confianza) if existing.match_confianza is not None else None,
            status="already_ingested",
        )
    except Exception:
        for p in doc_paths:
            p.unlink(missing_ok=True)
        raise

    # 5. Evaluar auto-vinculación (ADR-5)
    auto_status = "created"
    if numero_oficio_norm is not None:
        try:
            auto_result = _evaluar_auto_vinculacion(db, correo, doc_rows)
            if auto_result:
                auto_status = "auto_linked"
        except Exception:
            # Si falla la auto-vinculación, degradar a PENDIENTE (no fallar la ingesta)
            correo.estado_revision = "PENDIENTE"

    db.commit()
    db.refresh(correo)

    return IngestaCorreoResultOut(
        id=correo.id,
        estado_revision=correo.estado_revision,  # type: ignore[arg-type]
        proceso_id=correo.proceso_id,
        match_confianza=float(correo.match_confianza) if correo.match_confianza is not None else None,
        status=auto_status,
    )


# ---------------------------------------------------------------------------
# Core: evaluar_auto_vinculacion (ADR-5)
# ---------------------------------------------------------------------------

def _evaluar_auto_vinculacion(
    db: Session,
    correo: IngestaCorreo,
    doc_rows: list[IngestaDocumento],
) -> bool:
    """Evalúa si el correo debe auto-vincularse a un proceso.

    Condiciones (AMBAS deben cumplirse):
    a) numero_oficio normalizado coincide EXACTO con un proceso existente (exactamente 1).
    b) similitud difusa nombre_servicio_normalizado ≥ AUTO_LINK_THRESHOLD (0.90).

    Si se cumplen: ejecuta vincular con APROBADO_AUTO.
    Retorna True si se auto-vinculó, False si se queda PENDIENTE.
    """
    if correo.numero_oficio is None:
        return False

    # Buscar procesos con ese oficio (NO soft-deleted)
    candidatos = db.execute(
        select(Proceso).where(
            Proceso.numero_oficio == correo.numero_oficio,
            Proceso.eliminado_en.is_(None),
        )
    ).scalars().all()

    if len(candidatos) != 1:
        # Sin candidato o más de uno → ambigüedad → PENDIENTE
        return False

    proceso = candidatos[0]

    # Evaluar similitud de nombre
    nombre_correo = correo.nombre_servicio_normalizado or ""
    nombre_proceso = normalizar_nombre_servicio(proceso.requerimiento)
    ratio = evaluar_similitud_nombre(nombre_correo, nombre_proceso)

    if ratio < AUTO_LINK_THRESHOLD:
        return False

    # Condiciones cumplidas → auto-vincular
    _vincular(
        db=db,
        correo=correo,
        proceso=proceso,
        doc_rows=doc_rows,
        usuario="INGESTA_AUTO",
        estado="APROBADO_AUTO",
        match_confianza=ratio,
    )
    return True


# ---------------------------------------------------------------------------
# Core: vincular (rutina común AUTO + manual)
# ---------------------------------------------------------------------------

def _vincular(
    db: Session,
    correo: IngestaCorreo,
    proceso: Proceso,
    doc_rows: list[IngestaDocumento],
    usuario: str,
    estado: str,
    match_confianza: Optional[float] = None,
) -> None:
    """Rutina común de vinculación (AUTO o manual).

    1. Ancla oficio al proceso si no tiene.
    2. Mueve adjuntos staging → proc_{id}/.
    3. Actualiza estado del correo.
    4. Registra audit en historial_cambios.

    Mueve archivos ANTES del commit; rollback de renames si algo falla.
    """
    upload_path = settings.upload_path

    # Anclar oficio al proceso si no tiene (UN oficio principal por proceso)
    if proceso.numero_oficio is None and correo.numero_oficio is not None:
        proceso.numero_oficio = correo.numero_oficio

    # Mover adjuntos: staging → proc_{proceso.id}/
    proc_destino = _proc_dir(proceso.id)
    moved: list[tuple[Path, Path]] = []  # (origen, destino)

    try:
        for doc_row in doc_rows:
            src = upload_path / doc_row.ruta_relativa
            dest = proc_destino / doc_row.nombre_almacenado
            assert_within_upload_dir(dest, upload_path)

            if src.exists():
                src.rename(dest)
                moved.append((src, dest))
            else:
                # Si el archivo no existe físicamente, igual actualizamos la ruta
                # (puede pasar en tests sin archivos reales)
                pass

            doc_row.ruta_relativa = str(Path(f"proc_{proceso.id}") / doc_row.nombre_almacenado)
            doc_row.proceso_id = proceso.id

    except Exception:
        # Rollback de renames
        for src, dest in moved:
            if dest.exists():
                dest.rename(src)
        raise

    # Actualizar estado del correo
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    correo.estado_revision = estado
    correo.proceso_id = proceso.id
    correo.revisado_por = usuario
    correo.revisado_en = now
    correo.match_confianza = match_confianza

    # Audit en historial_cambios (ADR-7)
    n_docs = len(doc_rows)
    detalle = f"ingesta_id={correo.id} vinculado a proceso_id={proceso.id}, {n_docs} doc(s)"
    if match_confianza is not None:
        detalle += f", match_confianza={match_confianza:.3f}"

    audit = HistorialCambio(
        proceso_id=proceso.id,
        campo_modificado="ingesta_vinculacion",
        valor_anterior=None,
        valor_nuevo=detalle,
        modificado_por=usuario,
    )
    db.add(audit)

    # Alcance B — Inferencia de avance de etapas (inferencia_etapas.py)
    # REFINAMIENTO FASE 3: usa inferir_avance_correo (por-etapa, datos reales, sin cascada).
    # Se ejecuta DENTRO del mismo transaction que _vincular.
    from app.services.inferencia_etapas import inferir_avance_correo
    inferir_avance_correo(db, proceso.id, correo, doc_rows)


# ---------------------------------------------------------------------------
# Public: aprobar_correo (camino manual)
# ---------------------------------------------------------------------------

def aprobar_correo(
    db: Session,
    ingesta_id: int,
    proceso_id: int,
    usuario: str,
    etapa_sugerida: str | None = None,
    estado_etapa: str = "COMPLETADO",
    fecha_documento=None,
    fecha_fin=None,
    responsable: str | None = None,
    oficio_correo: str | None = None,
    observaciones: str | None = None,
) -> IngestaCorreo:
    """Vincula manualmente un correo PENDIENTE a un proceso.

    Raises 404 si el correo no existe.
    Raises 409 si ya está APROBADO, APROBADO_AUTO o RECHAZADO.
    Raises 422 si el proceso no existe o está soft-deleted.
    """
    correo = db.get(IngestaCorreo, ingesta_id)
    if correo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Correo de ingesta {ingesta_id} no encontrado.",
        )

    if correo.estado_revision in ("APROBADO", "APROBADO_AUTO", "RECHAZADO"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El correo ya está en estado '{correo.estado_revision}'. No se puede aprobar nuevamente.",
        )

    # Validar proceso
    proceso = db.get(Proceso, proceso_id)
    if proceso is None or proceso.eliminado_en is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Proceso {proceso_id} no encontrado o fue eliminado.",
        )

    # Cargar documentos del correo
    doc_rows = db.execute(
        select(IngestaDocumento).where(IngestaDocumento.ingesta_correo_id == ingesta_id)
    ).scalars().all()

    if etapa_sugerida:
        correo.etapa_sugerida = etapa_sugerida
    if fecha_documento:
        correo.fecha_documento = fecha_documento
    if oficio_correo:
        correo.numero_oficio = oficio_correo

    # Valores confirmados en la UI para poblar el registro de etapa inferido.
    # No requieren columnas nuevas: viven solo durante esta transacción.
    correo.estado_etapa_sugerido = estado_etapa
    correo.fecha_fin_sugerida = fecha_fin
    correo.responsable_sugerido = responsable
    correo.oficio_correo_sugerido = oficio_correo
    correo.observaciones_sugeridas = observaciones

    _vincular(
        db=db,
        correo=correo,
        proceso=proceso,
        doc_rows=list(doc_rows),
        usuario=usuario,
        estado="APROBADO",
        match_confianza=None,
    )

    db.commit()
    db.refresh(correo)
    return correo


# ---------------------------------------------------------------------------
# Public: rechazar_correo
# ---------------------------------------------------------------------------

def rechazar_correo(
    db: Session,
    ingesta_id: int,
    motivo: Optional[str],
    usuario: str,
) -> IngestaCorreo:
    """Marca un correo PENDIENTE como RECHAZADO.

    Raises 404 si no existe.
    Raises 409 si ya está en estado resuelto (APROBADO/APROBADO_AUTO/RECHAZADO).
    """
    correo = db.get(IngestaCorreo, ingesta_id)
    if correo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Correo de ingesta {ingesta_id} no encontrado.",
        )

    if correo.estado_revision in ("APROBADO", "APROBADO_AUTO", "RECHAZADO"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El correo ya está en estado '{correo.estado_revision}'. No se puede rechazar.",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    correo.estado_revision = "RECHAZADO"
    correo.motivo_rechazo = motivo
    correo.revisado_por = usuario
    correo.revisado_en = now

    db.commit()
    db.refresh(correo)
    return correo


# ---------------------------------------------------------------------------
# Public: restaurar_correo
# ---------------------------------------------------------------------------

def restaurar_correo(
    db: Session,
    ingesta_id: int,
    usuario: str,
) -> IngestaCorreo:
    """Restaura un correo RECHAZADO a PENDIENTE para nueva revisión."""
    correo = db.get(IngestaCorreo, ingesta_id)
    if correo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Correo de ingesta {ingesta_id} no encontrado.",
        )

    if correo.estado_revision != "RECHAZADO":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Solo se pueden restaurar correos RECHAZADO. Estado actual: '{correo.estado_revision}'.",
        )

    correo.estado_revision = "PENDIENTE"
    correo.motivo_rechazo = None
    correo.revisado_por = usuario
    correo.revisado_en = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(correo)
    return correo


# ---------------------------------------------------------------------------
# Public: desvincular_correo
# ---------------------------------------------------------------------------

def desvincular_correo(
    db: Session,
    ingesta_id: int,
    usuario: str,
) -> IngestaCorreo:
    """Revierte la vinculación de un correo (AUTO o manual) → PENDIENTE.

    Mueve adjuntos de proc_{id}/ → ingesta_staging/.
    NO revierte numero_oficio del proceso (decisión MVP).
    Registra audit.

    Raises 404 si el correo no existe.
    Raises 409 si el correo no está en APROBADO o APROBADO_AUTO.
    """
    correo = db.get(IngestaCorreo, ingesta_id)
    if correo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Correo de ingesta {ingesta_id} no encontrado.",
        )

    if correo.estado_revision not in ("APROBADO", "APROBADO_AUTO"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"El correo está en estado '{correo.estado_revision}'. "
                f"Solo se pueden desvincular correos APROBADO o APROBADO_AUTO."
            ),
        )

    proceso_id_anterior = correo.proceso_id
    upload_path = settings.upload_path
    staging = _staging_dir()

    # Cargar documentos vinculados
    doc_rows = db.execute(
        select(IngestaDocumento).where(IngestaDocumento.ingesta_correo_id == ingesta_id)
    ).scalars().all()

    # Mover adjuntos: proc_{id}/ → staging (rename inverso)
    moved: list[tuple[Path, Path]] = []

    try:
        for doc_row in doc_rows:
            src = upload_path / doc_row.ruta_relativa
            dest = staging / doc_row.nombre_almacenado
            assert_within_upload_dir(dest, upload_path)

            if src.exists():
                src.rename(dest)
                moved.append((src, dest))

            doc_row.ruta_relativa = str(Path("ingesta_staging") / doc_row.nombre_almacenado)
            doc_row.proceso_id = None

    except Exception:
        # Rollback de renames
        for src, dest in moved:
            if dest.exists():
                dest.rename(src)
        raise

    # Actualizar estado del correo
    correo.estado_revision = "PENDIENTE"
    correo.proceso_id = None
    correo.match_confianza = None
    correo.revisado_por = None
    correo.revisado_en = None

    # Alcance B — Revertir inferencia de etapas (INF-03)
    # Se ejecuta ANTES del commit, dentro del mismo transaction.
    if proceso_id_anterior is not None:
        from app.services.inferencia_etapas import revertir_avance
        revertir_avance(db, proceso_id_anterior, correo_id=ingesta_id)

    # Audit de la desvinculación (ADR-7)
    n_docs = len(list(doc_rows))
    audit = HistorialCambio(
        proceso_id=proceso_id_anterior,
        campo_modificado="ingesta_desvinculacion",
        valor_anterior=f"ingesta_id={ingesta_id} vinculado a proceso_id={proceso_id_anterior}",
        valor_nuevo=f"desvinculado por {usuario}, {n_docs} doc(s) movidos a staging",
        modificado_por=usuario,
    )
    db.add(audit)

    db.commit()
    db.refresh(correo)
    return correo


# ---------------------------------------------------------------------------
# Public: get_pendientes / get_pendientes_entry_ids
# ---------------------------------------------------------------------------

_ESTADOS_VISIBLES = {"PENDIENTE", "APROBADO_AUTO"}


def get_pendientes(db: Session, estado: str = "PENDIENTE") -> list[IngestaCorreo]:
    """Retorna correos filtrados por estado (PENDIENTE o APROBADO_AUTO).

    Args:
        estado: 'PENDIENTE' (default, bandeja normal) o 'APROBADO_AUTO'
                (para auditar auto-vinculados y poder desvincularlos).
                Otros valores son rechazados por el router con 422.
    """
    correos = db.execute(
        select(IngestaCorreo)
        .where(IngestaCorreo.estado_revision == estado)
        .order_by(IngestaCorreo.creado_en.desc())
    ).scalars().all()
    return list(correos)


def get_pendientes_entry_ids(db: Session) -> list[str]:
    """Retorna solo los entry_ids de todos los correos no-RECHAZADOS.

    Usado por el orquestador para dedup eficiente (GET /ingesta/pendientes?solo_entry_ids=true).
    Incluye PENDIENTE, APROBADO y APROBADO_AUTO para no reprocesar ninguno.
    """
    from sqlalchemy import column
    rows = db.execute(
        select(IngestaCorreo.entry_id).where(
            IngestaCorreo.estado_revision.in_(["PENDIENTE", "APROBADO", "APROBADO_AUTO"])
        )
    ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Public: corregir_correo (PATCH parcial)
# ---------------------------------------------------------------------------

def corregir_correo(
    db: Session,
    ingesta_id: int,
    correcciones: CorreoCorreccionIn,
) -> IngestaCorreo:
    """Aplica correcciones inline a un correo PENDIENTE.

    Raises 404 si no existe.
    Raises 409 si el correo no está PENDIENTE.
    """
    correo = db.get(IngestaCorreo, ingesta_id)
    if correo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Correo de ingesta {ingesta_id} no encontrado.",
        )

    if correo.estado_revision != "PENDIENTE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Solo se pueden corregir correos PENDIENTE. Estado actual: '{correo.estado_revision}'.",
        )

    campos_editables = (
        "nombre_servicio",
        "nombre_servicio_normalizado",
        "numero_oficio",
        "numero_oficio_raw",
        "siaf",
        "proveedor",
        "tipo",
        "fecha_documento",
        "fecha_recepcion",
    )
    for campo in campos_editables:
        valor = getattr(correcciones, campo, None)
        if valor is not None:
            setattr(correo, campo, valor)

    db.commit()
    db.refresh(correo)
    return correo


# ---------------------------------------------------------------------------
# Public: get_correo / guardar_propuesta_correo
# ---------------------------------------------------------------------------

def get_correo(db: Session, ingesta_id: int) -> IngestaCorreo:
    """Retorna un correo de ingesta por id o 404."""
    correo = db.get(IngestaCorreo, ingesta_id)
    if correo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Correo de ingesta {ingesta_id} no encontrado.",
        )
    return correo


def _resolver_proceso_sugerido(db: Session, data: dict) -> int | None:
    """Resuelve un proceso sugerido por id interno o codigo visible YYYY-NNN."""
    proceso_id = data.get("proceso_sugerido_id")
    if proceso_id is not None:
        proceso = db.get(Proceso, proceso_id)
        if proceso is None or proceso.eliminado_en is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Proceso sugerido {proceso_id} no encontrado o fue eliminado.",
            )
        return int(proceso_id)

    haystack = " ".join(
        str(data.get(campo) or "")
        for campo in (
            "proceso_sugerido_codigo",
            "resumen_sugerido",
            "relevancia_motivos",
        )
    )
    match = re.search(r"\b20\d{2}-\d{3,4}\b", haystack)
    if not match:
        return None

    codigo = match.group(0)
    proceso = db.execute(
        select(Proceso).where(
            Proceso.id_proceso == codigo,
            Proceso.eliminado_en.is_(None),
        )
    ).scalar_one_or_none()
    return proceso.id if proceso is not None else None


def guardar_propuesta_correo(
    db: Session,
    ingesta_id: int,
    propuesta: CorreoPropuestaIn,
    usuario: str,
) -> IngestaCorreo:
    """Guarda una propuesta de clasificacion sin aprobar el correo.

    Pensado para Claude/MCP: puede sugerir proceso, etapa, resumen y oficio,
    pero la vinculacion final sigue pasando por el modal de aprobacion.
    """
    correo = get_correo(db, ingesta_id)
    if correo.estado_revision != "PENDIENTE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Solo se pueden guardar propuestas en correos PENDIENTE. "
                f"Estado actual: '{correo.estado_revision}'."
            ),
        )

    data = propuesta.model_dump(exclude_unset=True)

    proceso_id = _resolver_proceso_sugerido(db, data)
    if proceso_id is not None:
        data["proceso_sugerido_id"] = proceso_id
    elif data.get("relevancia_score") is not None and data["relevancia_score"] > 0.75:
        data["relevancia_score"] = 0.75

    if "relevancia_motivos" in data:
        motivos = data.get("relevancia_motivos")
        if motivos and not str(motivos).startswith("[CLAUDE]"):
            data["relevancia_motivos"] = f"[CLAUDE] {motivos}"

    for campo in (
        "proceso_sugerido_id",
        "etapa_sugerida",
        "fase_sugerida",
        "resumen_sugerido",
        "relevancia_motivos",
        "relevancia_score",
        "fecha_documento",
    ):
        if campo in data:
            setattr(correo, campo, data[campo])

    if "numero_oficio" in data:
        valor = data["numero_oficio"]
        correo.numero_oficio = normalizar_oficio(valor) if valor else None

    audit = HistorialCambio(
        proceso_id=proceso_id,
        campo_modificado="ingesta_propuesta",
        valor_anterior=None,
        valor_nuevo=f"ingesta_id={ingesta_id} propuesta por {usuario}",
        modificado_por=usuario,
    )
    db.add(audit)

    db.commit()
    db.refresh(correo)
    return correo


# ---------------------------------------------------------------------------
# Public: get_documentos_proceso
# ---------------------------------------------------------------------------

def get_documentos_proceso(db: Session, proceso_id: int) -> list[IngestaDocumento]:
    """Retorna todos los documentos vinculados a un proceso (pestaña Documentos)."""
    docs = db.execute(
        select(IngestaDocumento)
        .where(IngestaDocumento.proceso_id == proceso_id)
        .order_by(IngestaDocumento.id)
    ).scalars().all()
    return list(docs)


def get_correos_etapa(
    db: Session,
    proceso_id: int,
    codigo_etapa: str,
) -> list[IngestaCorreo]:
    """Retorna correos aprobados vinculados a una etapa del proceso."""
    codigo = codigo_etapa.strip()
    correos = db.execute(
        select(IngestaCorreo)
        .where(
            IngestaCorreo.proceso_id == proceso_id,
            IngestaCorreo.etapa_sugerida == codigo,
            IngestaCorreo.estado_revision.in_(["APROBADO", "APROBADO_AUTO"]),
        )
        .order_by(IngestaCorreo.revisado_en.desc().nullslast(), IngestaCorreo.creado_en.desc())
    ).scalars().all()
    return list(correos)
