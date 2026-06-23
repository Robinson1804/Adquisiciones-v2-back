"""Live demo of C3b rules via TestClient."""
from decimal import Decimal

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.config import settings
from app.core.security import hash_password
from app.main import app
from app.models.etapa import EtapaRegistro
from app.models.montos import MontosProceso
from app.models.usuario import Usuario

engine = create_engine(settings.DATABASE_URL, future=True)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# Clean slate
with engine.connect() as conn:
    with conn.begin():
        conn.execute(text("DELETE FROM historial_cambios"))
        conn.execute(text("DELETE FROM procesos"))
        conn.execute(text("DELETE FROM usuarios WHERE username = 'demo_c3b'"))

db = Session()
u = Usuario(
    username="demo_c3b",
    nombre_completo="Demo C3b",
    rol="EDITOR",
    activo=True,
    area=None,
    password_hash=hash_password("DemoPass1!"),
)
db.add(u)
db.commit()

with TestClient(app) as client:
    r = client.post(
        "/auth/login",
        json={"username": "demo_c3b", "password": "DemoPass1!"},
    )
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create proceso with PENDIENTE CMN
    proc = client.post(
        "/procesos",
        json={
            "requerimiento": "Demo C3b live",
            "tipo": "SERVICIO",
            "areas_usuarias": ["DTDIS"],
            "anno": 2026,
            "cmn_por_area": [{"area": "DTDIS", "cmn_adjunto": "PENDIENTE"}],
        },
        headers=headers,
    ).json()
    pid = proc["id"]
    print(f"Proceso created: id={pid}")

    # DEMO 1: R1 blocked — E01 cmn_adjunto=PENDIENTE
    r1 = client.post(
        f"/procesos/{pid}/etapas",
        json={
            "codigo_etapa": "E02",
            "nombre_etapa": "TDR",
            "estado_etapa": "COMPLETADO",
            "fecha_inicio": "2026-06-01",
        },
        headers=headers,
    )
    print(
        f"\n[DEMO 1 - R1 BLOCKED] POST E02 (E01 cmn=PENDIENTE):"
        f" HTTP {r1.status_code}"
    )
    print(f"  detail: {r1.json()['detail']}")

    # DEMO 2: E19 → montos_proceso updated
    r2 = client.post(
        f"/procesos/{pid}/etapas",
        json={
            "codigo_etapa": "E19",
            "nombre_etapa": "OCS",
            "estado_etapa": "COMPLETADO",
            "fecha_inicio": "2026-06-01",
            "nro_ocs": "OCS-2026-042",
            "monto_ocs": "148000.00",
            "plazo_entrega": 30,
        },
        headers=headers,
    )
    print(f"\n[DEMO 2 - E19 montos] POST E19: HTTP {r2.status_code}")
    if r2.status_code == 201:
        etapas_data = client.get(
            f"/procesos/{pid}/etapas", headers=headers
        ).json()
        e19 = next(e for e in etapas_data["etapas"] if e["cod"] == "E19")
        print(f"  E19 vencimiento_ocs (derived): {e19['filas'][0]['vencimiento_ocs']}")
        db.expire_all()
        m = db.execute(
            select(MontosProceso).where(MontosProceso.proceso_id == pid)
        ).scalars().first()
        print(
            f"  montos_proceso: nro_ocs={m.nro_ocs},"
            f" monto_ocs={m.monto_ocs}, plazo={m.plazo_entrega}"
        )

db.close()

# Cleanup
with engine.connect() as conn:
    with conn.begin():
        conn.execute(text("DELETE FROM historial_cambios"))
        conn.execute(text("DELETE FROM procesos"))
        conn.execute(text("DELETE FROM usuarios WHERE username = 'demo_c3b'"))

print("\nDemo complete.")
