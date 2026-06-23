from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config import settings
from app.database import get_db
from app.routers import auth as auth_router
from app.routers import etapas as etapas_router
from app.routers import procesos as procesos_router
from app.routers.archivos import router as archivos_router
from app.routers import dashboard as dashboard_router
from app.routers import export as export_router
from app.routers.firma_secuencial import router as firma_secuencial_router
from app.routers.ingesta import router as ingesta_router
from app.routers import tiempos as tiempos_router

app = FastAPI(title="Adquisiciones TIC API", version="0.1.0")

app.include_router(auth_router.router)
app.include_router(procesos_router.router)
app.include_router(etapas_router.router, tags=["etapas"])
app.include_router(archivos_router)
app.include_router(dashboard_router.router)
app.include_router(export_router.router)
app.include_router(firma_secuencial_router)
app.include_router(ingesta_router)
app.include_router(tiempos_router.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "database": "disconnected"},
        )
