"""Service layer for file attachments — C3c.

Security contract (enforced here, not in the router):
- MIME type validated against ALLOWED_CONTENT_TYPES allowlist.
- File size validated against MAX_UPLOAD_BYTES.
- Stage must be in CODIGOS_CON_ADJUNTOS (key stages only).
- Storage path is NEVER derived from the client filename; uses uuid4().hex + safe ext.
- Client filename is sanitized (basename only) and stored for display only.
- Path traversal on read: assert path resolves within UPLOAD_DIR before serving.
- Orphan files: write to disk first; insert+commit; unlink on commit failure.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.archivo import EtapaArchivo
from app.models.etapa import EtapaRegistro
from app.services.etapas_catalogo import CODIGOS_CON_ADJUNTOS

# ---------------------------------------------------------------------------
# MIME type → safe extension whitelist (D12)
# ---------------------------------------------------------------------------

ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Return a safe display filename from the client-supplied name.

    - Takes the basename only (strips any path components).
    - Detects path traversal attempts (raises 400).
    - Replaces spaces with underscores.
    - Truncates to 200 chars.
    """
    basename = os.path.basename(name)
    if ".." in name or name.startswith("/") or name.startswith("\\"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nombre de archivo inválido: posible path traversal detectado.",
        )
    safe = basename.replace(" ", "_")[:200]
    return safe or "archivo"


def get_safe_ext(content_type: str) -> str:
    """Return the safe file extension for an allowed MIME type.

    Raises 422 if the content_type is not in the allowlist.
    """
    ext = ALLOWED_CONTENT_TYPES.get(content_type)
    if ext is None:
        allowed = ", ".join(ALLOWED_CONTENT_TYPES.keys())
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Tipo de archivo no permitido: '{content_type}'. "
                f"Tipos permitidos: {allowed}"
            ),
        )
    return ext


def assert_within_upload_dir(path: Path, upload_dir: Path) -> None:
    """Raise 404 if the resolved path escapes UPLOAD_DIR (path traversal guard)."""
    resolved = path.resolve()
    upload_resolved = upload_dir.resolve()
    if not str(resolved).startswith(str(upload_resolved)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archivo no encontrado.",
        )


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

async def save_archivo(
    db: Session,
    etapa_id: int,
    file: UploadFile,
    usuario: str,
    settings,
) -> EtapaArchivo:
    """Validate and persist a file attachment for an etapa.

    Order: validate → write disk → insert+commit → unlink on failure.
    """
    # 1. Validate MIME type
    content_type = file.content_type or ""
    get_safe_ext(content_type)  # raises 422 if invalid
    ext = ALLOWED_CONTENT_TYPES[content_type]

    # 2. Validate file size — read all bytes and check
    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Archivo demasiado grande: {len(content)} bytes. "
                f"Máximo permitido: {settings.MAX_UPLOAD_BYTES} bytes (10 MB)."
            ),
        )

    # 3. Validate etapa exists and accepts adjuntos
    etapa = db.get(EtapaRegistro, etapa_id)
    if etapa is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Etapa {etapa_id} no encontrada.",
        )
    if etapa.codigo_etapa not in CODIGOS_CON_ADJUNTOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Esta etapa no admite adjuntos.",
        )

    # 4. Sanitize display filename (NEVER used in path)
    nombre_original = sanitize_filename(file.filename or "archivo")

    # 5. Build storage path — uuid + safe ext from MIME (never from client)
    nombre_almacenado = uuid.uuid4().hex + ext
    proc_dir = settings.upload_path / f"proc_{etapa.proceso_id}"
    proc_dir.mkdir(parents=True, exist_ok=True)
    dest_path = proc_dir / nombre_almacenado

    # Security: confirm dest_path is still within UPLOAD_DIR (should always be
    # true given our construction, but defense in depth)
    assert_within_upload_dir(dest_path, settings.upload_path)

    # 6. Write file to disk FIRST; DB insert is flushed (router calls commit)
    dest_path.write_bytes(content)
    try:
        ruta_relativa = str(dest_path.relative_to(settings.upload_path))
        row = EtapaArchivo(
            etapa_id=etapa_id,
            nombre_original=nombre_original,
            nombre_almacenado=nombre_almacenado,
            ruta_relativa=ruta_relativa,
            content_type=content_type,
            tamano_bytes=len(content),
            subido_por=usuario,
        )
        db.add(row)
        db.flush()
        return row
    except Exception:
        # Unlink orphan file if DB flush/insert fails
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
        raise


def list_archivos(db: Session, etapa_id: int) -> list[EtapaArchivo]:
    """Return all attachment metadata rows for an etapa (ordered by id)."""
    return db.execute(
        select(EtapaArchivo)
        .where(EtapaArchivo.etapa_id == etapa_id)
        .order_by(EtapaArchivo.id)
    ).scalars().all()


def delete_archivo(db: Session, archivo_id: int, settings) -> None:
    """Delete an attachment: remove file from disk (tolerate absence) + DB row.

    Does NOT commit — caller (router) is responsible for db.commit().
    """
    row = db.get(EtapaArchivo, archivo_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Archivo {archivo_id} no encontrado.",
        )
    # Remove file from disk (tolerate if already gone)
    file_path = settings.upload_path / row.ruta_relativa
    if file_path.exists():
        file_path.unlink(missing_ok=True)

    db.delete(row)
    db.flush()
