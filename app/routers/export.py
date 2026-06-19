"""Export router — C5: Excel + PDF download endpoints.

Prefix: /export (root — NOT /api, per project convention).
Auth: get_current_user (any authenticated role — VIEWER, EDITOR, ADMIN).
Read-only — no DB mutations.

Design authority: design #162.
"""
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.services import export_service
from app.services.export_service import _get_active_proceso_or_404

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/excel")
def export_excel(
    anno: int = Query(..., description="Año fiscal (4 dígitos)"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> StreamingResponse:
    """Export all procesos + etapas + montos for the given year as .xlsx.

    Returns an empty-but-valid workbook when no procesos exist for the year.
    422 when anno is missing.
    """
    data = export_service.build_excel(db, anno)
    filename = f"adquisiciones_tic_{anno}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/proceso/{proceso_id}/pdf")
def export_pdf(
    proceso_id: int,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> StreamingResponse:
    """Export one proceso as a PDF executive summary.

    404 when proceso_id does not exist or is soft-deleted.
    """
    # Fetch proceso first to get the human-readable id for the filename
    proceso = _get_active_proceso_or_404(db, proceso_id)
    data = export_service.build_pdf(db, proceso_id)
    filename = f"proceso_{proceso.id_proceso}.pdf"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
