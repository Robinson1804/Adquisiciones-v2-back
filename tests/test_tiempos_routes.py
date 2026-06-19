"""TDD — RED phase tests for GET /procesos/{id}/tiempos endpoint.

Three cases: 200 viewer (contract keys), 404 missing proceso, 401 unauthenticated.
All tests use dashboard_test via client+db_session from conftest.py.
"""
from __future__ import annotations

import pytest

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers: dict) -> dict:
    """Create a test proceso and return the response body."""
    payload = {
        "requerimiento": "Test tiempos endpoint",
        "tipo": "SERVICIO",
        "areas_usuarias": ["DTDIS"],
        "anno": 2026,
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

class TestTiemposRoute:

    def test_200_viewer_contract(self, client, db_session, editor_headers, viewer_headers):
        """VIEWER GET /procesos/{id}/tiempos → 200 with required contract keys."""
        proc = _create_proceso(client, editor_headers)
        pid = proc["id"]

        # Add two dated milestones so we get at least one interval
        _insert_etapa(db_session, pid, "E01a", "2026-03-04")
        _insert_etapa(db_session, pid, "E02b", "2026-03-24")

        resp = client.get(f"/procesos/{pid}/tiempos", headers=viewer_headers)
        assert resp.status_code == 200, resp.text

        body = resp.json()
        assert "intervalos" in body
        assert "total_dias" in body
        assert "por_area" in body
        assert "cuello_de_botella" in body

    def test_404_missing_proceso(self, client, viewer_headers):
        """Non-existent proceso_id → 404."""
        resp = client.get("/procesos/999999/tiempos", headers=viewer_headers)
        assert resp.status_code == 404, resp.text

    def test_401_unauthenticated(self, client):
        """No auth token → 401."""
        resp = client.get("/procesos/1/tiempos")
        assert resp.status_code == 401, resp.text
