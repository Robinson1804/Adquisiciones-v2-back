"""Tests for sequential chain prerequisite enforcement — C3c Part 1.

Covers:
- SC-01: E03 registers when E02 COMPLETADO
- SC-02: E03 blocked (409) when E02 not COMPLETADO
- SC-03: E07 registers without E05/E06 (loops optional)
- SC-04: E05/bucle blocked (409) when E04 not registered
- SC-05: E09 registers without E08a/E08b (loops optional), needs E08 APROBADO
- SC-06: E12 blocked when E11 has PENDIENTE row (R3)
- SC-07: E12 registers when all E11 COMPLETADO
- SC-08/09: Mid-chain blocks (E14 without E13)
- Regression: R1, R5, R7 still enforce their 409 cases
"""
import pytest
from app.models.etapa import EtapaRegistro


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers, areas=None):
    """Create a proceso via the API; returns the response JSON dict."""
    areas = areas or ["AREA_A"]
    resp = client.post(
        "/procesos",
        json={
            "requerimiento": "Test Cadena Secuencial",
            "tipo": "SERVICIO",
            "areas_usuarias": areas,
            "anno": 2026,
            "cmn_por_area": [{"area": a, "cmn_adjunto": "SI"} for a in areas],
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"Failed to create proceso: {resp.text}"
    return resp.json()


def _register(client, proceso_id, cod, headers, estado="COMPLETADO", **extra):
    """Register a stage; return the response."""
    body = {"codigo_etapa": cod, "nombre_etapa": f"Test {cod}", "estado_etapa": estado}
    body.update(extra)
    return client.post(f"/procesos/{proceso_id}/etapas", json=body, headers=headers)


def _insert_etapa(db_session, proceso_id, cod, estado="COMPLETADO", **kw):
    """Bypass API and insert a stage row directly (for setup only)."""
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=f"Direct {cod}",
        area_responsable="TEST",
        es_bucle=False,
        estado_etapa=estado,
        nro_ronda=1,
        registrado_por="testeditor",
        **kw,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _build_chain_up_to(db_session, proceso_id, stop_before):
    """Insert chain rows directly up to (but not including) stop_before.

    flujo-real-otin-v2: E01 replaced by E01a/E01b/E01c in chain.
    """
    from app.services.etapas_catalogo import CADENA
    for cod in CADENA:
        if cod == stop_before:
            break
        kw = {}
        if cod == "E01c":
            kw = {"area_usuaria": "AREA_A"}
        elif cod == "E08":
            kw = {"resultado_eval": "APROBADO"}
        elif cod == "E09":
            kw = {"monto_cert": "1000.00"}
        elif cod == "E10":
            kw = {"resultado_eval": "CON_PRESUPUESTO"}
        elif cod == "E11":
            kw = {"area_usuaria": "AREA_A", "monto_cert": "500.00"}
        elif cod == "E19":
            kw = {"nro_ocs": "OCS-001", "monto_ocs": "1000.00", "plazo_entrega": 30}
        _insert_etapa(db_session, proceso_id, cod, **kw)


# ---------------------------------------------------------------------------
# SC-02: E03 blocked when E02 not COMPLETADO
# ---------------------------------------------------------------------------

def test_e03_blocked_without_e02_completado(client, editor_headers, db_session):
    """SC-02: E03 returns 409 when E02b (direct prereq) is not COMPLETADO."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    # flujo-real-otin-v2: E03 prereq is E02b (not E02 directly)
    _insert_etapa(db_session, pid, "E01a")
    _insert_etapa(db_session, pid, "E01b")
    _insert_etapa(db_session, pid, "E01c", area_usuaria="AREA_A")
    _insert_etapa(db_session, pid, "E02")
    _insert_etapa(db_session, pid, "E02b", estado="EN_CURSO")  # NOT COMPLETADO

    resp = _register(client, pid, "E03", editor_headers)
    assert resp.status_code == 409, resp.json()
    assert "E02b" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# SC-01: E03 registers when E02b COMPLETADO
# ---------------------------------------------------------------------------

def test_e03_registers_with_e02_completado(client, editor_headers, db_session):
    """SC-01: E03 registers (201) when full prereq chain E01a→E01b→E01c→E02→E02b is COMPLETADO."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    # flujo-real-otin-v2: prereq chain
    _insert_etapa(db_session, pid, "E01a")
    _insert_etapa(db_session, pid, "E01b")
    _insert_etapa(db_session, pid, "E01c", area_usuaria="AREA_A")
    _insert_etapa(db_session, pid, "E02")
    _insert_etapa(db_session, pid, "E02b")

    resp = _register(client, pid, "E03", editor_headers)
    assert resp.status_code == 201, resp.json()


# ---------------------------------------------------------------------------
# SC-03: E07 registers without E05/E06 (loops are optional)
# ---------------------------------------------------------------------------

def _insert_new_chain_prereqs(db_session, proceso_id):
    """Insert E01a/E01b/E01c as COMPLETADO (new chain root for flujo-real-otin-v2)."""
    _insert_etapa(db_session, proceso_id, "E01a")
    _insert_etapa(db_session, proceso_id, "E01b")
    _insert_etapa(db_session, proceso_id, "E01c", area_usuaria="AREA_A")


def test_e07_registers_without_e05_e06(client, editor_headers, db_session):
    """SC-03: E07 is registrable with E04 COMPLETADO even without E05/E06."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    _insert_new_chain_prereqs(db_session, pid)
    for cod in ["E02", "E02b", "E03", "E04"]:
        _insert_etapa(db_session, pid, cod)

    # No E05/E06 inserted — E07 should still register
    resp = _register(client, pid, "E07", editor_headers)
    assert resp.status_code == 201, resp.json()


# ---------------------------------------------------------------------------
# SC-04: E05/bucle blocked when E04 not registered (R6 regression)
# ---------------------------------------------------------------------------

def test_e05_bucle_blocked_without_e04(client, editor_headers, db_session):
    """SC-04: E05 loop returns 409 when E04 is not COMPLETADO (R6)."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    _insert_new_chain_prereqs(db_session, pid)
    # Insert E02/E02b/E03 but NOT E04
    for cod in ["E02", "E02b", "E03"]:
        _insert_etapa(db_session, pid, cod)

    resp = client.post(
        f"/procesos/{pid}/etapas/E05/bucle",
        json={"motivo_bucle": "test"},
        headers=editor_headers,
    )
    assert resp.status_code == 409, resp.json()


# ---------------------------------------------------------------------------
# SC-05: E09 registers without E08a/E08b (needs E08 APROBADO — R7)
# ---------------------------------------------------------------------------

def test_e09_registers_without_e08a_e08b(client, editor_headers, db_session):
    """SC-05: E09 registers with E08 APROBADO even if E08a/E08b absent."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    _insert_new_chain_prereqs(db_session, pid)
    for cod in ["E02", "E02b", "E03", "E04", "E07"]:
        _insert_etapa(db_session, pid, cod)
    _insert_etapa(db_session, pid, "E08", resultado_eval="APROBADO")

    # No E08a/E08b — E09 should succeed
    resp = _register(client, pid, "E09", editor_headers, monto_cert="1000.00")
    assert resp.status_code == 201, resp.json()


# ---------------------------------------------------------------------------
# SC-06: E12 blocked when E11 has PENDIENTE area row (R3 regression)
# ---------------------------------------------------------------------------

def test_e12_blocked_with_e11_pendiente(client, editor_headers, db_session):
    """SC-06: E12 returns 409 when an E11 area row is still PENDIENTE."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    _insert_new_chain_prereqs(db_session, pid)
    for cod, kw in [
        ("E02", {}), ("E02b", {}), ("E03", {}), ("E04", {}), ("E07", {}),
        ("E08", {"resultado_eval": "APROBADO"}),
        ("E09", {"monto_cert": "1000.00"}),
        ("E10", {"resultado_eval": "CON_PRESUPUESTO"}),
    ]:
        _insert_etapa(db_session, pid, cod, **kw)

    # E11 with PENDIENTE area row
    _insert_etapa(db_session, pid, "E11", estado="PENDIENTE",
                  area_usuaria="AREA_A", monto_cert="500.00")

    resp = _register(client, pid, "E12", editor_headers)
    assert resp.status_code == 409, resp.json()


# ---------------------------------------------------------------------------
# SC-07: E12 registers when all E11 COMPLETADO
# ---------------------------------------------------------------------------

def test_e12_registers_with_all_e11_completado(client, editor_headers, db_session):
    """SC-07: E12 registers (201) when all E11 area rows are COMPLETADO."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    _insert_new_chain_prereqs(db_session, pid)
    for cod, kw in [
        ("E02", {}), ("E02b", {}), ("E03", {}), ("E04", {}), ("E07", {}),
        ("E08", {"resultado_eval": "APROBADO"}),
        ("E09", {"monto_cert": "1000.00"}),
        ("E10", {"resultado_eval": "CON_PRESUPUESTO"}),
    ]:
        _insert_etapa(db_session, pid, cod, **kw)

    # E11 with ALL COMPLETADO
    _insert_etapa(db_session, pid, "E11", area_usuaria="AREA_A", monto_cert="500.00")

    resp = _register(client, pid, "E12", editor_headers)
    assert resp.status_code == 201, resp.json()


# ---------------------------------------------------------------------------
# SC-09: E14 blocked without E13 (mid-chain)
# ---------------------------------------------------------------------------

def test_e14_blocked_without_e13(client, editor_headers, db_session):
    """SC-09: E14 returns 409 when E13 is not COMPLETADO."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    _insert_new_chain_prereqs(db_session, pid)
    for cod, kw in [
        ("E02", {}), ("E02b", {}), ("E03", {}), ("E04", {}), ("E07", {}),
        ("E08", {"resultado_eval": "APROBADO"}),
        ("E09", {"monto_cert": "1000.00"}),
        ("E10", {"resultado_eval": "CON_PRESUPUESTO"}),
        ("E11", {"area_usuaria": "AREA_A", "monto_cert": "500.00"}),
        ("E12", {}),
        # E13 intentionally skipped
    ]:
        _insert_etapa(db_session, pid, cod, **kw)

    # Attempt E14 without E13
    resp = _register(client, pid, "E14", editor_headers)
    assert resp.status_code == 409, resp.json()
    assert "E13" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Regression: R7 still enforces E08 APROBADO for E09
# ---------------------------------------------------------------------------

def test_r7_regression_e09_blocked_without_aprobado(client, editor_headers, db_session):
    """R7 regression: E09 returns 409 when E08 exists but resultado_eval != APROBADO."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    _insert_new_chain_prereqs(db_session, pid)
    for cod in ["E02", "E02b", "E03", "E04", "E07"]:
        _insert_etapa(db_session, pid, cod)

    # E08 COMPLETADO but resultado_eval = "NO_APROBADO"
    _insert_etapa(db_session, pid, "E08", resultado_eval="NO_APROBADO")

    resp = _register(client, pid, "E09", editor_headers, monto_cert="1000.00")
    assert resp.status_code == 409, resp.json()


# ---------------------------------------------------------------------------
# Regression: E02 blocked if E01c PENDIENTE for some area
# (replaces old R1 cmn_adjunto regression)
# ---------------------------------------------------------------------------

def test_r1_regression_e02_blocked_without_cmn_si(client, editor_headers, db_session):
    """Regression: E02 returns 409 when E01c is PENDIENTE for at least one area."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    _insert_etapa(db_session, pid, "E01a")
    _insert_etapa(db_session, pid, "E01b")
    # E01c exists but PENDIENTE for AREA_A → blocks E02
    _insert_etapa(db_session, pid, "E01c", estado="PENDIENTE", area_usuaria="AREA_A")

    resp = _register(client, pid, "E02", editor_headers)
    assert resp.status_code == 409, resp.json()
