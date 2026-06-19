"""TDD — tests for GET /tiempos/matriz route.

All tests target dashboard_test ONLY (enforced by conftest.py).
adquisiciones_tic is NEVER referenced here.

Cases:
  (a) 200 + contract keys for authenticated viewer
  (b) unauthenticated → 401
  (c) anno+estado filters reduce filas count
  (d) no matching procesos → filas=[], promedios all null
"""
from __future__ import annotations

import pytest

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers: dict, anno: int = 2026, estado: str = "EN PROCESO") -> dict:
    """Create a test proceso and return the response body."""
    payload = {
        "requerimiento": f"Test matriz tiempos {anno}",
        "tipo": "SERVICIO",
        "areas_usuarias": ["DTDIS"],
        "anno": anno,
        "cmn_por_area": [{"area": "DTDIS", "cmn_adjunto": "SI"}],
    }
    resp = client.post("/procesos", json=payload, headers=headers)
    assert resp.status_code == 201, f"Failed to create proceso: {resp.text}"
    return resp.json()


def _insert_etapa(db_session, proceso_id: int, cod: str, fecha_fin: str) -> EtapaRegistro:
    """Insert a minimal completed EtapaRegistro with a fecha_fin."""
    from app.services.etapas_catalogo import ETAPAS_CATALOGO
    spec = ETAPAS_CATALOGO.get(cod)
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=spec.nombre if spec else cod,
        area_responsable=spec.area_responsable if spec else "OTIN",
        estado_etapa="COMPLETADO",
        fecha_fin=fecha_fin,
        nro_ronda=1,
        es_bucle=False,
        registrado_por="testsetup",
    )
    db_session.add(row)
    db_session.flush()
    return row


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestMatrizTiemposRoute:

    def test_200_viewer_contract_keys(
        self, client, db_session, editor_headers, viewer_headers
    ):
        """Authenticated VIEWER GET /tiempos/matriz → 200 with required contract keys."""
        proc = _create_proceso(client, editor_headers)
        pid = proc["id"]

        # Add two dated milestones for one interval
        _insert_etapa(db_session, pid, "E01a", "2026-01-01")
        _insert_etapa(db_session, pid, "E01c", "2026-01-11")

        resp = client.get("/tiempos/matriz", headers=viewer_headers)
        assert resp.status_code == 200, resp.text

        body = resp.json()
        # Top-level contract keys
        assert "columnas" in body
        assert "filas" in body
        assert "promedios" in body
        assert "promedio_total" in body

        # columnas should have 8 entries (7 hito + TOTAL)
        assert len(body["columnas"]) == 8
        # The last column is TOTAL
        assert body["columnas"][-1]["key"] == "total"
        assert body["columnas"][-1]["cod"] is None

        # filas must have our single proceso
        assert len(body["filas"]) == 1
        fila = body["filas"][0]
        assert "proceso_id" in fila
        assert "id_proceso" in fila
        assert "requerimiento" in fila
        assert "celdas" in fila
        assert "total_dias" in fila
        # celdas length == 7 (hito cols only, TOTAL is separate)
        assert len(fila["celdas"]) == 7

        # promedios length == 8 (aligned to columnas including TOTAL)
        assert len(body["promedios"]) == 8

    def test_401_unauthenticated(self, client):
        """No auth token → 401."""
        resp = client.get("/tiempos/matriz")
        assert resp.status_code == 401, resp.text

    def test_anno_estado_filters_reduce_filas(
        self, client, db_session, editor_headers, viewer_headers
    ):
        """anno+estado filters apply and reduce the number of returned filas."""
        # Create 2 procesos in 2026 and 1 in 2025
        p1 = _create_proceso(client, editor_headers, anno=2026)
        p2 = _create_proceso(client, editor_headers, anno=2026)
        p3 = _create_proceso(client, editor_headers, anno=2025)

        for pid in [p1["id"], p2["id"], p3["id"]]:
            _insert_etapa(db_session, pid, "E01a", "2026-01-01")
            _insert_etapa(db_session, pid, "E01c", "2026-01-11")

        # Filter: anno=2026 → only 2 filas
        resp = client.get("/tiempos/matriz?anno=2026", headers=viewer_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["filas"]) == 2

        # Filter: anno=2025 → only 1 fila
        resp2 = client.get("/tiempos/matriz?anno=2025", headers=viewer_headers)
        assert resp2.status_code == 200, resp2.text
        assert len(resp2.json()["filas"]) == 1

    def test_no_matching_procesos_returns_empty(
        self, client, viewer_headers
    ):
        """No procesos match filter → filas=[], promedios all null."""
        resp = client.get(
            "/tiempos/matriz?anno=1900",  # no procesos will ever be from 1900
            headers=viewer_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["filas"] == []
        assert all(v is None for v in body["promedios"])
        assert body["promedio_total"] is None

    def test_fila_contract_includes_pim_estado_tipo(
        self, client, db_session, editor_headers, viewer_headers
    ):
        """Each fila in the response must include pim, estado, and tipo fields."""
        proc = _create_proceso(client, editor_headers)
        pid = proc["id"]
        _insert_etapa(db_session, pid, "E01a", "2026-01-01")
        _insert_etapa(db_session, pid, "E01c", "2026-01-11")

        resp = client.get("/tiempos/matriz", headers=viewer_headers)
        assert resp.status_code == 200, resp.text
        fila = resp.json()["filas"][0]
        # Fields must be present (values may be null for pim)
        assert "pim" in fila
        assert "estado" in fila
        assert "tipo" in fila
        # estado was "EN PROCESO" from _create_proceso
        assert fila["estado"] == "EN PROCESO"
        # tipo was "SERVICIO" from _create_proceso
        assert fila["tipo"] == "SERVICIO"

    def test_q_filter_by_id_proceso(
        self, client, db_session, editor_headers, viewer_headers
    ):
        """q param filters filas by id_proceso (case-insensitive contains)."""
        p1 = _create_proceso(client, editor_headers, anno=2026)
        p2 = _create_proceso(client, editor_headers, anno=2026)

        for pid in [p1["id"], p2["id"]]:
            _insert_etapa(db_session, pid, "E01a", "2026-01-01")
            _insert_etapa(db_session, pid, "E01c", "2026-01-11")

        # Filter by exact id_proceso of p1
        resp = client.get(
            f"/tiempos/matriz?q={p1['id_proceso']}",
            headers=viewer_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["filas"]) == 1
        assert body["filas"][0]["id_proceso"] == p1["id_proceso"]

    def test_q_filter_by_requerimiento(
        self, client, db_session, editor_headers, viewer_headers
    ):
        """q param filters filas by requerimiento (case-insensitive contains)."""
        p1 = _create_proceso(client, editor_headers, anno=2026)
        p2 = _create_proceso(client, editor_headers, anno=2026)

        for pid in [p1["id"], p2["id"]]:
            _insert_etapa(db_session, pid, "E01a", "2026-01-01")
            _insert_etapa(db_session, pid, "E01c", "2026-01-11")

        # "Test matriz tiempos" is the requerimiento prefix; both match a
        # suffix unique to anno+ordering — just verify the full text is contained
        # Use a unique substring that only requerimiento contains (not id_proceso)
        resp = client.get(
            "/tiempos/matriz?q=Test+matriz+tiempos",
            headers=viewer_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Both procesos' requerimientos match "Test matriz tiempos"
        assert len(body["filas"]) == 2

    def test_q_no_match_returns_empty(
        self, client, db_session, editor_headers, viewer_headers
    ):
        """q param that matches nothing → filas=[]."""
        _create_proceso(client, editor_headers, anno=2026)
        resp = client.get(
            "/tiempos/matriz?q=XYZNONEXISTENT9999",
            headers=viewer_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["filas"] == []
