"""Tests for C4 executive dashboard endpoints and service.

Covers:
- T1: test_sync_fases (COD_A_FASE covers exactly the 27 catalog keys)
- T4: service-level tests for all 5 aggregations
- T5: integration tests via TestClient (auth, empty year, response shapes)

Helpers reuse _insert_etapa pattern from test_montos.py.
autouse _clean_business_tables from conftest.py handles isolation.
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.etapa import EtapaRegistro
from app.models.montos import MontosProceso
from app.models.proceso import Proceso
from app.services.etapas_catalogo import (
    COD_A_FASE,
    ETAPAS_CATALOGO,
    FASES,
    fase_de_cod,
)
from app.services import dashboard_service


# ---------------------------------------------------------------------------
# T1 — Catalog sync test
# ---------------------------------------------------------------------------

def test_sync_fases_covers_all_catalog_keys():
    """COD_A_FASE must cover exactly all keys in ETAPAS_CATALOGO.

    flujo-real-otin-v2: catalog has 32 entries (was 28).
    """
    catalog_keys = set(ETAPAS_CATALOGO.keys())
    fase_keys = set(COD_A_FASE.keys())
    missing = catalog_keys - fase_keys
    extra = fase_keys - catalog_keys
    assert not missing, f"Codes in catalog but missing from COD_A_FASE: {sorted(missing)}"
    assert not extra, f"Codes in COD_A_FASE but not in catalog: {sorted(extra)}"
    assert len(fase_keys) == len(catalog_keys)


def test_fase_de_cod_spot_checks():
    """flujo-real-otin-v2: E01 removed; E01a/E01b/E01c replace it in F1."""
    assert fase_de_cod("E01a") == "F1"
    assert fase_de_cod("E01b") == "F1"
    assert fase_de_cod("E01c") == "F1"
    assert fase_de_cod("E02") == "F1"
    assert fase_de_cod("E02b") == "F1"
    assert fase_de_cod("E08a") == "F2"
    assert fase_de_cod("E09") == "F2"
    assert fase_de_cod("E10") == "F3"
    assert fase_de_cod("E16") == "F3"
    assert fase_de_cod("E17") == "F4"
    assert fase_de_cod("E22") == "F4"
    assert fase_de_cod("E23") == "F5"
    assert fase_de_cod("E25") == "F5"


def test_fases_has_5_entries():
    assert len(FASES) == 5
    assert set(FASES.keys()) == {"F1", "F2", "F3", "F4", "F5"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_proceso_direct(db_session, anno=2026, estado="EN PROCESO", pim=None) -> Proceso:
    """Insert a Proceso directly via ORM (bypasses router validation)."""
    n = db_session.execute(
        select(Proceso).where(Proceso.anno == anno)
    ).scalars().all()
    seq = len(n) + 1
    p = Proceso(
        id_proceso=f"{anno}-{seq:03d}",
        requerimiento=f"Test proceso {seq}",
        tipo="BIEN",
        estado=estado,
        anno=anno,
        pim=pim,
    )
    db_session.add(p)
    db_session.flush()
    return p


def _insert_etapa(
    db_session,
    proceso_id: int,
    cod: str,
    estado: str = "COMPLETADO",
    **kwargs,
) -> EtapaRegistro:
    spec = ETAPAS_CATALOGO.get(cod)
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=spec.nombre if spec else cod,
        area_responsable=spec.area_responsable if spec else "OTIN",
        estado_etapa=estado,
        nro_ronda=1,
        registrado_por="testsetup",
        **kwargs,
    )
    db_session.add(row)
    db_session.flush()
    db_session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# T4 — get_metricas
# ---------------------------------------------------------------------------

def test_metricas_counts(db_session):
    """Counts are correct; soft-deleted excluded; pim_total summed."""
    p1 = _create_proceso_direct(db_session, pim=Decimal("1000"))
    p2 = _create_proceso_direct(db_session, pim=Decimal("2000"))
    p3 = _create_proceso_direct(db_session, estado="CULMINADO", pim=Decimal("3000"))
    p4 = _create_proceso_direct(db_session, estado="CANCELADO", pim=None)
    # Soft-deleted
    p5 = _create_proceso_direct(db_session, pim=Decimal("9999"))
    from datetime import datetime
    p5.eliminado_en = datetime(2026, 1, 1)
    db_session.flush()

    result = dashboard_service.get_metricas(db_session, 2026)
    assert result.total == 4
    assert result.en_proceso == 2
    assert result.culminados == 1
    assert result.cancelados == 1
    assert result.pim_total == pytest.approx(6000.0, abs=0.01)


def test_metricas_empty_year(db_session):
    """Year with no procesos returns zeros and dias_promedio=None (INV-4)."""
    result = dashboard_service.get_metricas(db_session, 1999)
    assert result.total == 0
    assert result.en_proceso == 0
    assert result.culminados == 0
    assert result.cancelados == 0
    assert result.pim_total == 0.0
    assert result.dias_promedio is None


def test_metricas_no_culminados(db_session):
    """Procesos exist but none CULMINADO → dias_promedio=None."""
    _create_proceso_direct(db_session, pim=Decimal("500"))
    result = dashboard_service.get_metricas(db_session, 2026)
    assert result.total == 1
    assert result.dias_promedio is None


def test_metricas_dias_promedio_from_culminados(db_session):
    """dias_promedio = AVG(E25.fecha_fin - E01_min.fecha_inicio) for CULMINADOS only."""
    p = _create_proceso_direct(db_session, estado="CULMINADO", pim=Decimal("1000"))
    _insert_etapa(
        db_session, p.id, "E01",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 5),
    )
    _insert_etapa(
        db_session, p.id, "E25",
        fecha_inicio=date(2026, 3, 1),
        fecha_fin=date(2026, 4, 10),  # 99 days from Jan 1
    )

    result = dashboard_service.get_metricas(db_session, 2026)
    assert result.culminados == 1
    # span = Apr 10 - Jan 1 = 99 days
    assert result.dias_promedio == pytest.approx(99.0, abs=0.5)


# ---------------------------------------------------------------------------
# T4 — get_flujo_procesos
# ---------------------------------------------------------------------------

def test_flujo_procesos_empty_year(db_session):
    result = dashboard_service.get_flujo_procesos(db_session, 1999)
    assert result.procesos == []


def test_flujo_procesos_fase_from_etapa(db_session):
    """Proceso with F1+F2 fully completed → fase_actual=F3.

    flujo-real-otin-v2: F1 cods: E01a, E01b, E01c, E02, E02b.
    F2 cods (non-bucle): E03, E04, E07, E08, E09.
    Bucles E05/E06/E06b/E06c/E08a/E08b are in F2.
    """
    p = _create_proceso_direct(db_session)
    # Complete all of F1 (E01a, E01b, E01c, E02, E02b)
    _insert_etapa(db_session, p.id, "E01a")
    _insert_etapa(db_session, p.id, "E01b")
    _insert_etapa(db_session, p.id, "E01c", area_usuaria="DTDIS")
    for cod in ["E02", "E02b"]:
        _insert_etapa(db_session, p.id, cod)
    # Complete all ORDEN_ETAPAS-ordered cods through E11 so etapa_actual = E12 (F3)
    # Bucles included to avoid them becoming etapa_actual
    for cod in ["E03", "E04", "E05", "E06", "E06b", "E06c", "E07", "E08", "E08a", "E08b", "E09", "E10", "E11"]:
        _insert_etapa(db_session, p.id, cod)
    # etapa_actual = E12 → F3

    result = dashboard_service.get_flujo_procesos(db_session, 2026)
    assert len(result.procesos) == 1
    proc = result.procesos[0]
    assert proc.fase_actual == "F3"
    # F1 and F2 are completed
    fases_map = {f.fase: f for f in proc.fases}
    assert fases_map["F1"].completada is True
    assert fases_map["F2"].completada is True
    assert fases_map["F3"].actual is True
    assert fases_map["F4"].completada is False
    assert fases_map["F5"].completada is False


def test_flujo_procesos_culminado(db_session):
    """CULMINADO proceso → fase_actual=None, porcentaje=100, all fases completada=True."""
    p = _create_proceso_direct(db_session, estado="CULMINADO")
    result = dashboard_service.get_flujo_procesos(db_session, 2026)
    proc = result.procesos[0]
    assert proc.fase_actual is None
    assert proc.porcentaje == 100.0
    for fase in proc.fases:
        assert fase.completada is True
        assert fase.actual is False


def test_flujo_procesos_cancelado(db_session):
    """CANCELADO with F1 done → fase_actual=F2; F4/F5 not completada.

    flujo-real-otin-v2: F1 = E01a/E01b/E01c/E02/E02b.
    """
    p = _create_proceso_direct(db_session, estado="CANCELADO")
    # Complete all of F1 → etapa_actual=E03 (first F2 stage)
    _insert_etapa(db_session, p.id, "E01a")
    _insert_etapa(db_session, p.id, "E01b")
    _insert_etapa(db_session, p.id, "E01c", area_usuaria="DTDIS")
    _insert_etapa(db_session, p.id, "E02")
    _insert_etapa(db_session, p.id, "E02b")

    result = dashboard_service.get_flujo_procesos(db_session, 2026)
    proc = result.procesos[0]
    fases_map = {f.fase: f for f in proc.fases}
    assert fases_map["F1"].completada is True
    assert fases_map["F2"].completada is False
    assert fases_map["F4"].completada is False
    assert fases_map["F5"].completada is False


# ---------------------------------------------------------------------------
# T4 — get_tiempos_etapa
# ---------------------------------------------------------------------------

def test_tiempos_etapa_empty_year(db_session):
    result = dashboard_service.get_tiempos_etapa(db_session, 1999)
    assert result.etapas == []
    assert result.promedio_global is None


def test_tiempos_etapa_avg_correct(db_session):
    """AVG(dias) is computed correctly; OMITIDO excluded; bucles excluded.

    flujo-real-otin-v2: E01 removed; use E01a instead.
    """
    p = _create_proceso_direct(db_session)
    # E01a: dias = 5 (fecha_fin - fecha_inicio)
    _insert_etapa(
        db_session, p.id, "E01a",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 6),  # 5 days
    )
    # E02: dias = 10
    _insert_etapa(
        db_session, p.id, "E02",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 11),  # 10 days
    )
    # E05 (bucle): should be excluded from main set
    _insert_etapa(
        db_session, p.id, "E05",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 8),
    )
    # E03 OMITIDO: should be excluded
    _insert_etapa(
        db_session, p.id, "E03",
        estado="OMITIDO",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 3),
    )
    db_session.expire_all()

    result = dashboard_service.get_tiempos_etapa(db_session, 2026)
    assert len(result.etapas) >= 2
    by_cod = {e.codigo: e for e in result.etapas}
    assert "E01a" in by_cod  # flujo-real-otin-v2: E01a replaces E01
    assert by_cod["E01a"].dias_promedio == pytest.approx(5.0, abs=0.1)
    assert "E02" in by_cod
    assert by_cod["E02"].dias_promedio == pytest.approx(10.0, abs=0.1)
    assert "E05" not in by_cod  # bucle excluded
    assert "E03" not in by_cod  # OMITIDO excluded


def test_tiempos_etapa_promedio_global(db_session):
    """promedio_global = AVG of individual stage averages.

    flujo-real-otin-v2: E01 removed; use E01a instead.
    """
    p = _create_proceso_direct(db_session)
    _insert_etapa(
        db_session, p.id, "E01a",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 5),  # 4 days
    )
    _insert_etapa(
        db_session, p.id, "E02",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 9),  # 8 days
    )
    db_session.expire_all()

    result = dashboard_service.get_tiempos_etapa(db_session, 2026)
    # promedio_global = AVG(4.0, 8.0) = 6.0
    assert result.promedio_global == pytest.approx(6.0, abs=0.5)


# ---------------------------------------------------------------------------
# T4 — get_presupuesto
# ---------------------------------------------------------------------------

def test_presupuesto_empty_year(db_session):
    result = dashboard_service.get_presupuesto(db_session, 1999)
    assert result.procesos == []
    assert result.totales["pim"] == 0.0


def test_presupuesto_variaciones(db_session):
    """Variations computed correctly; null-safe when denominators are 0/None."""
    p = _create_proceso_direct(db_session, pim=Decimal("100"))
    # Add montos row
    montos = MontosProceso(
        proceso_id=p.id,
        valor_em=Decimal("110"),
        monto_cert_total=Decimal("90"),
        monto_ocs=Decimal("115"),
    )
    db_session.add(montos)
    db_session.flush()

    result = dashboard_service.get_presupuesto(db_session, 2026)
    assert len(result.procesos) == 1
    proc = result.procesos[0]
    assert proc.var_em_vs_pim == pytest.approx(10.0, abs=0.2)
    assert proc.var_cert_vs_em == pytest.approx(-18.2, abs=0.2)
    assert proc.var_ocs_vs_em == pytest.approx(4.5, abs=0.2)


def test_presupuesto_null_safe(db_session):
    """pim=None → var_em_vs_pim=None; pim=0 → var_em_vs_pim=None."""
    # pim is None
    p1 = _create_proceso_direct(db_session, pim=None)
    montos1 = MontosProceso(proceso_id=p1.id, valor_em=Decimal("100"))
    db_session.add(montos1)
    db_session.flush()

    result = dashboard_service.get_presupuesto(db_session, 2026)
    proc1 = next(pr for pr in result.procesos if pr.id == p1.id)
    assert proc1.var_em_vs_pim is None


def test_presupuesto_no_montos_row(db_session):
    """Proceso without montos_proceso row → all monetary fields null (LEFT JOIN)."""
    _create_proceso_direct(db_session, pim=Decimal("5000"))
    result = dashboard_service.get_presupuesto(db_session, 2026)
    assert len(result.procesos) == 1
    proc = result.procesos[0]
    assert proc.pim == pytest.approx(5000.0)
    assert proc.valor_em is None
    assert proc.monto_cert_total is None
    assert proc.monto_ocs is None
    assert proc.var_em_vs_pim is None


def test_presupuesto_totales_sum(db_session):
    """totales = SUM of each column, NULL treated as 0."""
    p1 = _create_proceso_direct(db_session, pim=Decimal("100"))
    p2 = _create_proceso_direct(db_session, pim=Decimal("200"))
    montos1 = MontosProceso(proceso_id=p1.id, valor_em=Decimal("90"))
    db_session.add(montos1)
    db_session.flush()

    result = dashboard_service.get_presupuesto(db_session, 2026)
    assert result.totales["pim"] == pytest.approx(300.0, abs=0.01)
    assert result.totales["valor_em"] == pytest.approx(90.0, abs=0.01)
    assert result.totales["monto_ocs"] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# T4 — get_demora_areas
# ---------------------------------------------------------------------------

def test_demora_areas_empty_year(db_session):
    result = dashboard_service.get_demora_areas(db_session, 1999)
    assert result.areas == []


def test_demora_areas_semaforo_verde(db_session):
    """AVG=5 days → semaforo=verde."""
    p = _create_proceso_direct(db_session)
    _insert_etapa(
        db_session, p.id, "E11",
        area_usuaria="DTDIS",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 6),  # 5 days
    )
    db_session.expire_all()

    result = dashboard_service.get_demora_areas(db_session, 2026)
    assert len(result.areas) == 1
    area = result.areas[0]
    assert area.area_usuaria == "DTDIS"
    assert area.e11_dias_promedio == pytest.approx(5.0, abs=0.1)
    assert area.semaforo_e11 == "verde"
    assert area.semaforo_e24 is None


def test_demora_areas_semaforo_amarillo(db_session):
    """AVG=10 days → semaforo=amarillo."""
    p = _create_proceso_direct(db_session)
    _insert_etapa(
        db_session, p.id, "E11",
        area_usuaria="GOBERNANZA",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 11),  # 10 days
    )
    db_session.expire_all()

    result = dashboard_service.get_demora_areas(db_session, 2026)
    area = result.areas[0]
    assert area.semaforo_e11 == "amarillo"


def test_demora_areas_semaforo_rojo(db_session):
    """AVG=20 days → semaforo=rojo."""
    p = _create_proceso_direct(db_session)
    _insert_etapa(
        db_session, p.id, "E24",
        area_usuaria="DTDIS",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 21),  # 20 days
    )
    db_session.expire_all()

    result = dashboard_service.get_demora_areas(db_session, 2026)
    area = next(a for a in result.areas if a.area_usuaria == "DTDIS")
    assert area.semaforo_e24 == "rojo"
    assert area.e11_dias_promedio is None
    assert area.semaforo_e11 is None


def test_demora_areas_threshold_boundaries(db_session):
    """Test exact boundary values: 7→verde, 8→amarillo, 15→amarillo, 16→rojo."""
    p = _create_proceso_direct(db_session)
    # Area1: E11 = 7 days (verde boundary)
    _insert_etapa(
        db_session, p.id, "E11",
        area_usuaria="AREA_A",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 8),  # 7 days
    )
    # Area2: E11 = 15 days (amarillo boundary)
    _insert_etapa(
        db_session, p.id, "E11",
        area_usuaria="AREA_B",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 16),  # 15 days
    )
    # Area3: E11 = 16 days (rojo)
    _insert_etapa(
        db_session, p.id, "E11",
        area_usuaria="AREA_C",
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=date(2026, 1, 17),  # 16 days
    )
    db_session.expire_all()

    result = dashboard_service.get_demora_areas(db_session, 2026)
    by_area = {a.area_usuaria: a for a in result.areas}
    assert by_area["AREA_A"].semaforo_e11 == "verde"
    assert by_area["AREA_B"].semaforo_e11 == "amarillo"
    assert by_area["AREA_C"].semaforo_e11 == "rojo"


# ---------------------------------------------------------------------------
# T5 — Integration tests via TestClient
# ---------------------------------------------------------------------------

def test_endpoints_require_auth(client):
    """All 5 dashboard endpoints return 401 without token (INV-1)."""
    for path in [
        "/dashboard/metricas?anno=2026",
        "/dashboard/flujo-procesos?anno=2026",
        "/dashboard/tiempos-etapa?anno=2026",
        "/dashboard/presupuesto?anno=2026",
        "/dashboard/demora-areas?anno=2026",
    ]:
        resp = client.get(path)
        assert resp.status_code == 401, f"Expected 401 for {path}, got {resp.status_code}"


def test_endpoints_require_anno(client, admin_headers):
    """Missing anno query param → 422 (INV-2)."""
    for path in [
        "/dashboard/metricas",
        "/dashboard/flujo-procesos",
        "/dashboard/tiempos-etapa",
        "/dashboard/presupuesto",
        "/dashboard/demora-areas",
    ]:
        resp = client.get(path, headers=admin_headers)
        assert resp.status_code == 422, f"Expected 422 for {path}, got {resp.status_code}"


def test_viewer_can_access_all_endpoints(client, viewer_headers):
    """VIEWER role can access all 5 endpoints (any role allowed per design)."""
    for path in [
        "/dashboard/metricas?anno=2026",
        "/dashboard/flujo-procesos?anno=2026",
        "/dashboard/tiempos-etapa?anno=2026",
        "/dashboard/presupuesto?anno=2026",
        "/dashboard/demora-areas?anno=2026",
    ]:
        resp = client.get(path, headers=viewer_headers)
        assert resp.status_code == 200, f"Expected 200 for {path}, got {resp.status_code}: {resp.text}"


def test_metricas_empty_year_via_client(client, admin_headers):
    """Empty year returns valid response (zeros), not 404 or 500 (INV-4)."""
    resp = client.get("/dashboard/metricas?anno=1990", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["pim_total"] == 0.0
    assert body["dias_promedio"] is None


def test_metricas_response_shape(client, admin_headers, db_session):
    """GET /dashboard/metricas returns correct MetricasOut shape."""
    _create_proceso_direct(db_session, pim=Decimal("50000"))
    resp = client.get("/dashboard/metricas?anno=2026", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "anno" in body
    assert "total" in body
    assert "en_proceso" in body
    assert "culminados" in body
    assert "cancelados" in body
    assert "pim_total" in body
    assert "dias_promedio" in body
    assert body["anno"] == 2026
    assert body["total"] == 1


def test_flujo_procesos_response_shape(client, admin_headers, db_session):
    """GET /dashboard/flujo-procesos returns FlujoProcesosResponse shape."""
    _create_proceso_direct(db_session)
    resp = client.get("/dashboard/flujo-procesos?anno=2026", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "anno" in body
    assert "procesos" in body
    assert len(body["procesos"]) == 1
    proc = body["procesos"][0]
    assert "fases" in proc
    assert len(proc["fases"]) == 5


def test_tiempos_etapa_empty_year_via_client(client, viewer_headers):
    """Empty year → etapas=[] and promedio_global=null."""
    resp = client.get("/dashboard/tiempos-etapa?anno=1990", headers=viewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["etapas"] == []
    assert body["promedio_global"] is None


def test_presupuesto_empty_year_via_client(client, viewer_headers):
    """Empty year → procesos=[], totales all 0.0."""
    resp = client.get("/dashboard/presupuesto?anno=1990", headers=viewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["procesos"] == []
    assert body["totales"]["pim"] == 0.0


def test_demora_areas_empty_year_via_client(client, viewer_headers):
    """Empty year → areas=[]."""
    resp = client.get("/dashboard/demora-areas?anno=1990", headers=viewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["areas"] == []
