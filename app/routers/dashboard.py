"""Dashboard router — 5 read-only aggregation endpoints (C4).

Prefix: /dashboard (root — NOT /api, per gotcha #129).
Auth: get_current_user (any authenticated role — VIEWER, EDITOR, ADMIN).
anno: required query param (int); missing/invalid → 422 (INV-2).

All endpoints delegate to dashboard_service; no business logic here.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.schemas.dashboard import (
    DemoraAreasResponse,
    FlujoProcesosResponse,
    MetricasOut,
    PresupuestoResponse,
    TiemposEtapaResponse,
)
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/metricas", response_model=MetricasOut)
def metricas(
    anno: int = Query(..., description="Año fiscal (4 dígitos)"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> MetricasOut:
    """6 KPI cards: total, en_proceso, culminados, cancelados, pim_total, dias_promedio."""
    return dashboard_service.get_metricas(db, anno)


@router.get("/flujo-procesos", response_model=FlujoProcesosResponse)
def flujo_procesos(
    anno: int = Query(..., description="Año fiscal (4 dígitos)"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> FlujoProcesosResponse:
    """Per-proceso 5-phase mini-timeline with progress percentage."""
    return dashboard_service.get_flujo_procesos(db, anno)


@router.get("/tiempos-etapa", response_model=TiemposEtapaResponse)
def tiempos_etapa(
    anno: int = Query(..., description="Año fiscal (4 dígitos)"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> TiemposEtapaResponse:
    """AVG days per stage code + global average reference line."""
    return dashboard_service.get_tiempos_etapa(db, anno)


@router.get("/presupuesto", response_model=PresupuestoResponse)
def presupuesto(
    anno: int = Query(..., description="Año fiscal (4 dígitos)"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> PresupuestoResponse:
    """Per-proceso budget data with 3 variations + yearly totals."""
    return dashboard_service.get_presupuesto(db, anno)


@router.get("/demora-areas", response_model=DemoraAreasResponse)
def demora_areas(
    anno: int = Query(..., description="Año fiscal (4 dígitos)"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> DemoraAreasResponse:
    """AVG days per area in E11 + E24 with semáforo (verde/amarillo/rojo)."""
    return dashboard_service.get_demora_areas(db, anno)
