"""Router for ingesta de correos documental.

Endpoints (sin prefijo /api, igual que los routers existentes):
  POST   /ingesta/correos             — ingestar correo (ADMIN/EDITOR)
  GET    /ingesta/pendientes          — bandeja de revisión (auth)
  PATCH  /ingesta/{id}               — corrección inline (ADMIN/EDITOR)
  POST   /ingesta/{id}/aprobar        — aprobación manual (ADMIN/EDITOR)
  POST   /ingesta/{id}/rechazar       — rechazo (ADMIN/EDITOR)
  POST   /ingesta/{id}/desvincular    — reversibilidad (ADMIN/EDITOR)
  GET    /procesos/{id}/documentos    — índice documental del proceso (auth)
  GET    /ingesta/documentos/{doc_id} — descarga con guard path-traversal (auth)

Auth: require_role(ADMIN, EDITOR) para escritura; get_current_user para lectura.
"""
from __future__ import annotations

from pathlib import Path

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user, require_role
from app.models.ingesta import IngestaDocumento
from app.models.usuario import Usuario
from app.schemas.ingesta import (
    AprobarIn,
    CorreoCorreccionIn,
    CorreoIngestaIn,
    CorreoIngestaOut,
    DocumentoIngestaOut,
    IngestaCorreoResultOut,
    IngestaPendientesOut,
    RechazarIn,
)
from app.services import ingesta_service as svc
from app.services.archivos_service import assert_within_upload_dir
from app.config import settings

router = APIRouter(tags=["ingesta"])


# ---------------------------------------------------------------------------
# POST /ingesta/correos — ingestar correo (idempotente por entry_id)
# ---------------------------------------------------------------------------

@router.post(
    "/ingesta/correos",
    response_model=IngestaCorreoResultOut,
    status_code=status.HTTP_201_CREATED,
)
def post_correo(
    body: CorreoIngestaIn,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> IngestaCorreoResultOut:
    """Recibe un correo extraído + adjuntos en base64.

    Idempotente por entry_id:
    - Nuevo correo → 201 status='created' (o 'auto_linked' si auto-vinculó).
    - entry_id ya existente → 200 status='already_ingested'.
    """
    result = svc.ingestar_correo(db, body)

    if result.status == "already_ingested":
        # Cambiar el status_code a 200 manualmente (FastAPI no permite esto vía return)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=result.model_dump(),
        )

    return result


# ---------------------------------------------------------------------------
# GET /ingesta/pendientes — bandeja de revisión
# ---------------------------------------------------------------------------

_ESTADOS_BANDEJA = Literal["PENDIENTE", "APROBADO_AUTO"]


@router.get("/ingesta/pendientes")
def get_pendientes(
    solo_entry_ids: bool = Query(False, alias="solo_entry_ids"),
    estado: _ESTADOS_BANDEJA = Query("PENDIENTE"),  # type: ignore[assignment]
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
):
    """Lista correos de la bandeja filtrados por estado.

    Parámetros:
    - ?estado=PENDIENTE (default) → bandeja normal de revisión humana.
    - ?estado=APROBADO_AUTO → correos auto-vinculados para auditoría/desvinculación.
    - Otros valores → 422 (valores permitidos: PENDIENTE, APROBADO_AUTO).
    - ?solo_entry_ids=true → list[str] de entry_ids (para dedup del orquestador).
      Cuando se combina con ?estado, se ignora el estado (siempre retorna todos
      los no-RECHAZADOS, para que el orquestador no reprocese ninguno).
    """
    if solo_entry_ids:
        return svc.get_pendientes_entry_ids(db)

    correos = svc.get_pendientes(db, estado=estado)

    # Cargar documentos para cada correo
    correo_outs = []
    for c in correos:
        docs = db.execute(
            select(IngestaDocumento).where(IngestaDocumento.ingesta_correo_id == c.id)
        ).scalars().all()
        correo_out = CorreoIngestaOut.model_validate(c)
        correo_out = correo_out.model_copy(update={
            "documentos": [DocumentoIngestaOut.model_validate(d) for d in docs]
        })
        correo_outs.append(correo_out)

    return IngestaPendientesOut(items=correo_outs, total=len(correo_outs))


# ---------------------------------------------------------------------------
# PATCH /ingesta/{id} — corrección inline de campos extraídos
# ---------------------------------------------------------------------------

@router.patch("/ingesta/{ingesta_id}", response_model=CorreoIngestaOut)
def patch_correo(
    ingesta_id: int,
    body: CorreoCorreccionIn,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> CorreoIngestaOut:
    """Corrección inline de campos extraídos (solo PENDIENTE)."""
    correo = svc.corregir_correo(db, ingesta_id, body)
    docs = db.execute(
        select(IngestaDocumento).where(IngestaDocumento.ingesta_correo_id == ingesta_id)
    ).scalars().all()
    result = CorreoIngestaOut.model_validate(correo)
    result = result.model_copy(update={
        "documentos": [DocumentoIngestaOut.model_validate(d) for d in docs]
    })
    return result


# ---------------------------------------------------------------------------
# POST /ingesta/{id}/aprobar — aprobación manual
# ---------------------------------------------------------------------------

@router.post("/ingesta/{ingesta_id}/aprobar", response_model=CorreoIngestaOut)
def aprobar_correo(
    ingesta_id: int,
    body: AprobarIn,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> CorreoIngestaOut:
    """Vincula manualmente un correo PENDIENTE a un proceso.

    409 si ya está APROBADO/APROBADO_AUTO/RECHAZADO.
    422 si el proceso no existe o está soft-deleted.
    """
    correo = svc.aprobar_correo(db, ingesta_id, body.proceso_id, current_user.username)
    docs = db.execute(
        select(IngestaDocumento).where(IngestaDocumento.ingesta_correo_id == ingesta_id)
    ).scalars().all()
    result = CorreoIngestaOut.model_validate(correo)
    result = result.model_copy(update={
        "documentos": [DocumentoIngestaOut.model_validate(d) for d in docs]
    })
    return result


# ---------------------------------------------------------------------------
# POST /ingesta/{id}/rechazar — rechazo
# ---------------------------------------------------------------------------

@router.post("/ingesta/{ingesta_id}/rechazar", response_model=CorreoIngestaOut)
def rechazar_correo(
    ingesta_id: int,
    body: RechazarIn,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> CorreoIngestaOut:
    """Marca un correo PENDIENTE como RECHAZADO."""
    correo = svc.rechazar_correo(db, ingesta_id, body.motivo, current_user.username)
    return CorreoIngestaOut.model_validate(correo)


# ---------------------------------------------------------------------------
# POST /ingesta/{id}/desvincular — reversibilidad
# ---------------------------------------------------------------------------

@router.post("/ingesta/{ingesta_id}/desvincular", response_model=CorreoIngestaOut)
def desvincular_correo(
    ingesta_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_role("ADMIN", "EDITOR")),
) -> CorreoIngestaOut:
    """Revierte la vinculación de un correo (APROBADO o APROBADO_AUTO) → PENDIENTE.

    Mueve documentos de definitivo → staging.
    NO revierte numero_oficio del proceso (decisión MVP).
    409 si el correo no está APROBADO/APROBADO_AUTO.
    """
    correo = svc.desvincular_correo(db, ingesta_id, current_user.username)
    return CorreoIngestaOut.model_validate(correo)


# ---------------------------------------------------------------------------
# GET /procesos/{id}/documentos — índice documental del proceso
# ---------------------------------------------------------------------------

@router.get(
    "/procesos/{proceso_id}/documentos",
    response_model=list[DocumentoIngestaOut],
)
def get_documentos_proceso(
    proceso_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
) -> list[DocumentoIngestaOut]:
    """Lista documentos vinculados al proceso (pestaña Documentos)."""
    docs = svc.get_documentos_proceso(db, proceso_id)
    return [DocumentoIngestaOut.model_validate(d) for d in docs]


# ---------------------------------------------------------------------------
# GET /ingesta/documentos/{doc_id} — descarga con guard path-traversal
# ---------------------------------------------------------------------------

@router.get("/ingesta/documentos/{doc_id}")
def descargar_documento(
    doc_id: int,
    db: Session = Depends(get_db),
    _user: Usuario = Depends(get_current_user),
):
    """Descarga el binario de un documento de ingesta.

    Guard path-traversal: valida que la ruta resuelta esté dentro de UPLOAD_DIR.
    """
    doc = db.get(IngestaDocumento, doc_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Documento {doc_id} no encontrado.",
        )

    file_path = settings.upload_path / doc.ruta_relativa
    assert_within_upload_dir(file_path, settings.upload_path)

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archivo no encontrado en el sistema de archivos.",
        )

    return FileResponse(
        path=str(file_path),
        media_type=doc.content_type,
        filename=doc.nombre_original,
    )
