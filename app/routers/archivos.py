"""Archivos router — file attachment endpoints for key etapas.

Endpoints (mounted at root, no /api prefix — see C3a gotcha):
  POST /etapas/{etapa_id}/archivos    → upload file (ADMIN/EDITOR)
  GET  /etapas/{etapa_id}/archivos    → list attachments (authenticated)
  GET  /archivos/{archivo_id}         → download file (authenticated)
  DELETE /archivos/{archivo_id}       → delete file+row (ADMIN/EDITOR)

Security:
  - MIME type whitelist enforced in service.
  - File size enforced in service (≤10MB).
  - Storage path never derived from client filename (uuid + safe ext).
  - Download path-traversal guard in service.
  - Auth required for all endpoints; write ops require ADMIN or EDITOR.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies.auth import get_current_user, require_role
from app.models.archivo import EtapaArchivo
from app.models.usuario import Usuario
from app.schemas.archivo import ArchivoListOut, ArchivoOut
from app.services.archivos_service import (
    assert_within_upload_dir,
    delete_archivo,
    list_archivos,
    save_archivo,
)

router = APIRouter(tags=["archivos"])


# ---------------------------------------------------------------------------
# POST /etapas/{etapa_id}/archivos  — upload
# ---------------------------------------------------------------------------

@router.post(
    "/etapas/{etapa_id}/archivos",
    response_model=ArchivoOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_archivo(
    etapa_id: int,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> ArchivoOut:
    """Upload a file attachment to a key etapa.

    Validates: etapa exists, etapa accepts adjuntos, MIME type, file size.
    Stores under UPLOAD_DIR/proc_{proceso_id}/{uuid}{ext}.
    Returns ArchivoOut metadata (201).
    """
    row = await save_archivo(db, etapa_id, archivo, current_user.username, settings)
    db.commit()
    db.refresh(row)
    return ArchivoOut.model_validate(row)


# ---------------------------------------------------------------------------
# GET /etapas/{etapa_id}/archivos  — list
# ---------------------------------------------------------------------------

@router.get(
    "/etapas/{etapa_id}/archivos",
    response_model=ArchivoListOut,
)
def get_archivos_etapa(
    etapa_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
) -> ArchivoListOut:
    """List all file attachments for an etapa (empty list if none)."""
    rows = list_archivos(db, etapa_id)
    return ArchivoListOut(archivos=[ArchivoOut.model_validate(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /archivos/{archivo_id}  — download
# ---------------------------------------------------------------------------

@router.get("/archivos/{archivo_id}")
def download_archivo(
    archivo_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
) -> FileResponse:
    """Download a file attachment (authenticated).

    Returns file bytes with Content-Disposition: attachment.
    Path-traversal guard: resolves absolute path and asserts it stays within
    UPLOAD_DIR before serving.
    """
    row = db.get(EtapaArchivo, archivo_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Archivo {archivo_id} no encontrado.",
        )

    file_path = settings.upload_path / row.ruta_relativa
    assert_within_upload_dir(file_path, settings.upload_path)

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archivo no encontrado en el servidor.",
        )

    return FileResponse(
        path=str(file_path),
        media_type=row.content_type,
        filename=row.nombre_original,
        headers={
            "Content-Disposition": f'attachment; filename="{row.nombre_original}"'
        },
    )


# ---------------------------------------------------------------------------
# DELETE /archivos/{archivo_id}  — delete
# ---------------------------------------------------------------------------

@router.delete("/archivos/{archivo_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_archivo(
    archivo_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> Response:
    """Delete an attachment: removes filesystem file + etapa_archivos row."""
    delete_archivo(db, archivo_id, settings)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
