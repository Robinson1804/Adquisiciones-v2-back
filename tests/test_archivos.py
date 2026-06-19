"""Tests for file attachment endpoints — C3c Part 2.

Covers SC-10 through SC-22 plus security tests.

UPLOAD_DIR is overridden to tmp_path via monkeypatch so no real files
are written to the filesystem during tests. The service reads settings
at call time so the monkeypatch takes effect.

NOTE: save_archivo calls db.commit() internally (design decision: write disk
first then commit). Tests therefore use the client fixture which routes
through db.commit() — same as all other router tests.
"""
from __future__ import annotations

import io
import pytest
from pathlib import Path

from app.config import settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def override_upload_dir(tmp_path, monkeypatch):
    """Override UPLOAD_DIR on the settings singleton to use tmp_path."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    yield tmp_path


def _create_proceso(client, headers, areas=None):
    """Create a proceso via the API; returns the response JSON dict."""
    areas = areas or ["AREA_A"]
    resp = client.post(
        "/procesos",
        json={
            "requerimiento": "Test Adjuntos",
            "tipo": "SERVICIO",
            "areas_usuarias": areas,
            "anno": 2026,
            "cmn_por_area": [{"area": a, "cmn_adjunto": "SI"} for a in areas],
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Failed to create proceso: {resp.text}"
    return resp.json()


def _create_etapa(client, proceso_id, cod, headers, estado="COMPLETADO", **extra):
    """Register a stage via the API; returns the etapa ID."""
    body = {"codigo_etapa": cod, "nombre_etapa": f"Test {cod}", "estado_etapa": estado}
    body.update(extra)
    resp = client.post(f"/procesos/{proceso_id}/etapas", json=body, headers=headers)
    assert resp.status_code == 201, f"Failed to register {cod}: {resp.text}"
    return resp.json()["id"]


def pdf_bytes(size=1024):
    return b"%PDF-1.4 " + b"x" * (size - 9)


def pdf_file(filename="test.pdf", size=1024):
    return {"archivo": (filename, io.BytesIO(pdf_bytes(size)), "application/pdf")}


# ---------------------------------------------------------------------------
# Helpers to set up a proceso with an E02 etapa row ready for attachment
# Note: E02 requires E01 COMPLETADO (R1 + prereq). We insert E01 rows
# directly in the DB (already created by proceso creation) and mark them
# COMPLETADO, then register E02 via API.
# ---------------------------------------------------------------------------

def _setup_etapa_e02(client, editor_headers, db_session):
    """Create a proceso, insert E01a/E01b/E01c COMPLETADO, register E02; return (proceso, etapa_id).

    flujo-real-otin-v2: E01 replaced by E01a/E01b/E01c in the chain.
    """
    from app.models.etapa import EtapaRegistro

    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    # flujo-real-otin-v2: insert E01a/E01b/E01c COMPLETADO as prereqs for E02
    for cod, kw in [("E01a", {}), ("E01b", {}), ("E01c", {"area_usuaria": "AREA_A"})]:
        row = EtapaRegistro(
            proceso_id=pid,
            codigo_etapa=cod,
            nombre_etapa=f"Test {cod}",
            area_responsable="TEST",
            estado_etapa="COMPLETADO",
            nro_ronda=1,
            registrado_por="testsetup",
            **kw,
        )
        db_session.add(row)
    db_session.flush()

    # Register E02 via API (prereq chain E01a→E01b→E01c satisfied)
    etapa_id = _create_etapa(client, pid, "E02", editor_headers)
    return proc, etapa_id


def _setup_etapa_e04(client, editor_headers, db_session):
    """Set up a non-key stage E04 for rejection tests.

    flujo-real-otin-v2: E01 replaced by E01a/E01b/E01c.
    """
    from app.models.etapa import EtapaRegistro

    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    # flujo-real-otin-v2: insert E01a/E01b/E01c COMPLETADO
    for cod, kw in [("E01a", {}), ("E01b", {}), ("E01c", {"area_usuaria": "AREA_A"})]:
        row = EtapaRegistro(
            proceso_id=pid,
            codigo_etapa=cod,
            nombre_etapa=f"Test {cod}",
            area_responsable="TEST",
            estado_etapa="COMPLETADO",
            nro_ronda=1,
            registrado_por="testsetup",
            **kw,
        )
        db_session.add(row)
    db_session.flush()

    # E02, E02b, E03 required before E04 (flujo-real-otin-v2 chain)
    _create_etapa(client, pid, "E02", editor_headers)
    _create_etapa(client, pid, "E02b", editor_headers)
    _create_etapa(client, pid, "E03", editor_headers)
    etapa_id = _create_etapa(client, pid, "E04", editor_headers)
    return proc, etapa_id


# ---------------------------------------------------------------------------
# SC-10: Successful upload to key stage
# ---------------------------------------------------------------------------

def test_upload_pdf_to_key_stage(client, editor_headers, db_session, tmp_path):
    """SC-10: POST valid PDF to key stage E02 → 201, metadata correct."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    resp = client.post(
        f"/etapas/{etapa_id}/archivos",
        files=pdf_file(),
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.json()
    data = resp.json()
    assert data["etapa_id"] == etapa_id
    assert data["content_type"] == "application/pdf"
    assert data["tamano_bytes"] == 1024
    assert data["nombre_original"] == "test.pdf"

    # File must exist on disk
    files = list(tmp_path.rglob("*.pdf"))
    assert len(files) == 1


# ---------------------------------------------------------------------------
# SC-11: Oversized file rejected
# ---------------------------------------------------------------------------

def test_upload_oversized_rejected(client, editor_headers, db_session):
    """SC-11: POST 11MB file → 422 (too large)."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    big = 11 * 1024 * 1024
    resp = client.post(
        f"/etapas/{etapa_id}/archivos",
        files={"archivo": ("big.pdf", io.BytesIO(b"x" * big), "application/pdf")},
        headers=editor_headers,
    )
    assert resp.status_code == 422, resp.json()


# ---------------------------------------------------------------------------
# SC-12: Invalid MIME type rejected
# ---------------------------------------------------------------------------

def test_upload_invalid_mime_rejected(client, editor_headers, db_session):
    """SC-12: POST text/plain → 422."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    resp = client.post(
        f"/etapas/{etapa_id}/archivos",
        files={"archivo": ("bad.txt", io.BytesIO(b"hello"), "text/plain")},
        headers=editor_headers,
    )
    assert resp.status_code == 422, resp.json()
    assert "permitido" in resp.json()["detail"].lower() or "tipo" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# SC-13: .exe filename with PDF MIME → stored safely as .pdf
# ---------------------------------------------------------------------------

def test_upload_exe_filename_stored_as_pdf(client, editor_headers, db_session, tmp_path):
    """SC-13: .exe filename with PDF MIME → ext derived from MIME, stored as .pdf."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    resp = client.post(
        f"/etapas/{etapa_id}/archivos",
        files={"archivo": ("malware.exe", io.BytesIO(b"%PDF-1.4 content"), "application/pdf")},
        headers=editor_headers,
    )
    # Should succeed — MIME is valid; filename only for display
    assert resp.status_code == 201, resp.json()
    files = list(tmp_path.rglob("*.pdf"))
    assert len(files) == 1


# ---------------------------------------------------------------------------
# SC-14: Path traversal filename rejected
# ---------------------------------------------------------------------------

def test_upload_path_traversal_filename_rejected(client, editor_headers, db_session):
    """SC-14: POST with filename '../../etc/passwd.pdf' → 400."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    resp = client.post(
        f"/etapas/{etapa_id}/archivos",
        files={"archivo": ("../../etc/passwd.pdf", io.BytesIO(b"%PDF traversal"), "application/pdf")},
        headers=editor_headers,
    )
    assert resp.status_code == 400, resp.json()


# ---------------------------------------------------------------------------
# SC-15: Upload to non-key stage rejected
# ---------------------------------------------------------------------------

def test_upload_to_non_key_stage_rejected(client, editor_headers, db_session):
    """SC-15: POST to E04 (non-key) → 422 'Esta etapa no admite adjuntos'."""
    proc, etapa_id = _setup_etapa_e04(client, editor_headers, db_session)

    resp = client.post(
        f"/etapas/{etapa_id}/archivos",
        files=pdf_file(),
        headers=editor_headers,
    )
    assert resp.status_code == 422, resp.json()
    assert "admite" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# SC-16: List attachments
# ---------------------------------------------------------------------------

def test_list_archivos(client, editor_headers, db_session, tmp_path):
    """SC-16: GET /etapas/{id}/archivos returns correct count."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    for i in range(2):
        r = client.post(
            f"/etapas/{etapa_id}/archivos",
            files={"archivo": (f"file{i}.pdf", io.BytesIO(b"%PDF content"), "application/pdf")},
            headers=editor_headers,
        )
        assert r.status_code == 201, r.json()

    resp = client.get(f"/etapas/{etapa_id}/archivos", headers=editor_headers)
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert "archivos" in data
    assert len(data["archivos"]) == 2


# ---------------------------------------------------------------------------
# SC-17: Authenticated download returns file bytes
# ---------------------------------------------------------------------------

def test_download_archivo(client, editor_headers, db_session, tmp_path):
    """SC-17: GET /archivos/{id} returns file bytes with Content-Disposition."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    content = b"%PDF-1.4 authentic content here"
    up = client.post(
        f"/etapas/{etapa_id}/archivos",
        files={"archivo": ("myfile.pdf", io.BytesIO(content), "application/pdf")},
        headers=editor_headers,
    )
    assert up.status_code == 201, up.json()
    archivo_id = up.json()["id"]

    dl = client.get(f"/archivos/{archivo_id}", headers=editor_headers)
    assert dl.status_code == 200, dl.text
    assert dl.content == content
    assert "attachment" in dl.headers.get("content-disposition", "").lower()


# ---------------------------------------------------------------------------
# SC-18: Unauthenticated download → 401
# ---------------------------------------------------------------------------

def test_download_unauthenticated(client, editor_headers, db_session, tmp_path):
    """SC-18: GET /archivos/{id} without Authorization → 401."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    up = client.post(
        f"/etapas/{etapa_id}/archivos",
        files=pdf_file(),
        headers=editor_headers,
    )
    assert up.status_code == 201
    archivo_id = up.json()["id"]

    dl = client.get(f"/archivos/{archivo_id}")  # no auth
    assert dl.status_code == 401


# ---------------------------------------------------------------------------
# SC-19: Delete by EDITOR → 204, row gone, file gone
# ---------------------------------------------------------------------------

def test_delete_archivo_editor(client, editor_headers, db_session, tmp_path):
    """SC-19: DELETE /archivos/{id} by EDITOR → 204, file removed."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    up = client.post(
        f"/etapas/{etapa_id}/archivos",
        files=pdf_file(),
        headers=editor_headers,
    )
    assert up.status_code == 201
    archivo_id = up.json()["id"]

    d = client.delete(f"/archivos/{archivo_id}", headers=editor_headers)
    assert d.status_code == 204

    # File should be gone
    files = list(tmp_path.rglob("*.pdf"))
    assert len(files) == 0

    # Subsequent download → 404
    g = client.get(f"/archivos/{archivo_id}", headers=editor_headers)
    assert g.status_code == 404


# ---------------------------------------------------------------------------
# SC-20: Delete by VIEWER → 403
# ---------------------------------------------------------------------------

def test_delete_archivo_viewer_forbidden(client, viewer_headers, editor_headers, db_session, tmp_path):
    """SC-20: DELETE /archivos/{id} by VIEWER → 403."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    up = client.post(
        f"/etapas/{etapa_id}/archivos",
        files=pdf_file(),
        headers=editor_headers,
    )
    assert up.status_code == 201
    archivo_id = up.json()["id"]

    d = client.delete(f"/archivos/{archivo_id}", headers=viewer_headers)
    assert d.status_code == 403


# ---------------------------------------------------------------------------
# SC-21: Upload by VIEWER → 403
# ---------------------------------------------------------------------------

def test_upload_viewer_forbidden(client, viewer_headers, editor_headers, db_session):
    """SC-21: POST /etapas/{id}/archivos by VIEWER → 403."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    resp = client.post(
        f"/etapas/{etapa_id}/archivos",
        files=pdf_file(),
        headers=viewer_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# SC-22: ON DELETE CASCADE — deleting proceso removes etapa_archivos rows
# ---------------------------------------------------------------------------

def test_cascade_delete_proceso(client, editor_headers, db_session, tmp_path):
    """SC-22: Deleting proceso cascades to etapa_archivos rows."""
    from sqlalchemy import text
    from app.models.archivo import EtapaArchivo

    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)
    pid = proc["id"]

    up = client.post(
        f"/etapas/{etapa_id}/archivos",
        files=pdf_file(),
        headers=editor_headers,
    )
    assert up.status_code == 201
    archivo_id = up.json()["id"]

    # The row should exist now
    row = db_session.get(EtapaArchivo, archivo_id)
    assert row is not None

    # Delete proceso via cascade
    db_session.execute(text("DELETE FROM historial_cambios WHERE proceso_id = :pid"), {"pid": pid})
    db_session.execute(text("DELETE FROM procesos WHERE id = :pid"), {"pid": pid})
    db_session.flush()

    # DB session needs to refresh its identity map
    db_session.expire_all()
    row_after = db_session.get(EtapaArchivo, archivo_id)
    assert row_after is None


# ---------------------------------------------------------------------------
# Viewer can LIST (read-only OK)
# ---------------------------------------------------------------------------

def test_viewer_can_list_archivos(client, viewer_headers, editor_headers, db_session, tmp_path):
    """VIEWER can GET the list (read-only access)."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    up = client.post(
        f"/etapas/{etapa_id}/archivos",
        files=pdf_file(),
        headers=editor_headers,
    )
    assert up.status_code == 201

    resp = client.get(f"/etapas/{etapa_id}/archivos", headers=viewer_headers)
    assert resp.status_code == 200
    assert len(resp.json()["archivos"]) == 1


# ---------------------------------------------------------------------------
# Nonexistent archivo → 404
# ---------------------------------------------------------------------------

def test_download_nonexistent_archivo(client, editor_headers):
    """GET /archivos/99999 → 404."""
    resp = client.get("/archivos/99999", headers=editor_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Catalog: acepta_adjuntos exactness
# ---------------------------------------------------------------------------

def test_catalog_acepta_adjuntos_count():
    """CODIGOS_CON_ADJUNTOS must match the expected set.

    Updated set includes:
    - E06 (loop TDR correction — accepts docs)
    - E06b (new DTDIS visto-bueno loop)
    - E08 (OTIN response to technical eval)
    - E20 (provider notification)
    - E22 (service start / goods delivery)
    Previously False, now True per Part C requirements.
    """
    from app.services.etapas_catalogo import CODIGOS_CON_ADJUNTOS
    # flujo-real-otin-v2: E01 removed; E01a/E01b/E01c replace it
    expected = {
        "E01a", "E01b", "E01c",
        "E02", "E03",
        "E06", "E06b",
        "E07", "E08", "E09",
        "E11", "E13", "E14", "E15", "E16",
        "E19", "E20",
        "E22", "E24",
    }
    assert CODIGOS_CON_ADJUNTOS == expected, f"Mismatch: {CODIGOS_CON_ADJUNTOS}"


# ---------------------------------------------------------------------------
# DOCX upload accepted
# ---------------------------------------------------------------------------

def test_upload_docx(client, editor_headers, db_session, tmp_path):
    """DOCX files are accepted for key stages."""
    proc, etapa_id = _setup_etapa_e02(client, editor_headers, db_session)

    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    resp = client.post(
        f"/etapas/{etapa_id}/archivos",
        files={"archivo": ("doc.docx", io.BytesIO(b"PK fake docx content"), docx_mime)},
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.json()
    files = list(tmp_path.rglob("*.docx"))
    assert len(files) == 1
