"""Tests for the por_area idempotent upsert fix.

Regression: POST /procesos/{id}/etapas for E01/E11/E24 was always INSERTing a
new row.  Double-submit (double-click "Guardar") produced duplicate rows for
the same (proceso_id, codigo_etapa, area_usuaria), inflating budget totals.

Fix: when spec.por_area is True and area_usuaria matches an existing row,
UPDATE that row instead of INSERTing a duplicate.
"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.etapa import EtapaRegistro


# ---------------------------------------------------------------------------
# Shared helpers (mirror test_montos.py style)
# ---------------------------------------------------------------------------

def _create_proceso(client, headers, areas=None) -> dict:
    areas = areas or ["DTDIS"]
    payload = {
        "requerimiento": "Test upsert E11",
        "tipo": "BIEN",
        "areas_usuarias": areas,
        "anno": 2026,
        "cmn_por_area": [{"area": a, "cmn_adjunto": "SI"} for a in areas],
    }
    resp = client.post("/procesos", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_etapa(client, headers, proceso_id, cod, **extra):
    payload = {
        "codigo_etapa": cod,
        "nombre_etapa": f"Etapa {cod}",
        "fecha_inicio": "2026-06-01",
        "estado_etapa": "COMPLETADO",
    }
    payload.update(extra)
    return client.post(
        f"/procesos/{proceso_id}/etapas",
        json=payload,
        headers=headers,
    )


def _insert_etapa(db_session, proceso_id, cod, estado="COMPLETADO", **kwargs):
    from app.models.etapa import EtapaRegistro
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=f"Etapa {cod}",
        area_responsable="OTIN",
        estado_etapa=estado,
        nro_ronda=1,
        registrado_por="testsetup",
        **kwargs,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _setup_chain_up_to(db_session, proceso_id, stop_before_cod):
    """Reuse the same helper logic as test_montos to satisfy prerequisites."""
    from app.services.etapas_catalogo import CADENA
    existing_cods = {
        row.codigo_etapa
        for row in db_session.execute(
            select(EtapaRegistro).where(EtapaRegistro.proceso_id == proceso_id)
        ).scalars().all()
    }
    for cod in CADENA:
        if cod == stop_before_cod:
            break
        if cod == "E01":
            for row in db_session.execute(
                select(EtapaRegistro).where(
                    EtapaRegistro.proceso_id == proceso_id,
                    EtapaRegistro.codigo_etapa == "E01",
                )
            ).scalars().all():
                row.estado_etapa = "COMPLETADO"
            db_session.flush()
            continue
        if cod in existing_cods:
            continue
        kw: dict = {}
        if cod == "E08":
            kw = {"resultado_eval": "APROBADO"}
        elif cod == "E09":
            kw = {"monto_cert": "1000.00"}
        elif cod == "E10":
            kw = {"resultado_eval": "CON_PRESUPUESTO"}
        elif cod == "E11":
            kw = {"area_usuaria": "DTDIS", "monto_cert": "500.00"}
        elif cod == "E19":
            kw = {"nro_ocs": "OCS-SETUP", "monto_ocs": "1000.00", "plazo_entrega": 30}
        elif cod == "E22":
            kw = {"fecha_inicio": "2026-01-01"}
        _insert_etapa(db_session, proceso_id, cod, estado="COMPLETADO", **kw)
    db_session.flush()


# ---------------------------------------------------------------------------
# Core upsert test — E11 double-POST for the same area
# ---------------------------------------------------------------------------

def test_e11_double_post_same_area_no_duplicate(client, editor_headers, db_session):
    """Posting E11 twice for the same area must produce exactly ONE row.

    This is the canonical regression test for the reported bug.
    First call: monto_cert=5000.  Second call: monto_cert=9999.
    Expected: one row exists, monto_cert updated to 9999.
    """
    proc = _create_proceso(client, editor_headers, areas=["DTDIS"])
    _setup_chain_up_to(db_session, proc["id"], "E11")

    # First POST — creates the row
    r1 = _post_etapa(
        client, editor_headers, proc["id"], "E11",
        area_usuaria="DTDIS",
        monto_cert="5000.00",
    )
    assert r1.status_code == 201, r1.text

    # Second POST — same area, different monto (simulates double-click / re-edit)
    r2 = _post_etapa(
        client, editor_headers, proc["id"], "E11",
        area_usuaria="DTDIS",
        monto_cert="9999.00",
    )
    # Still returns 201 (endpoint always 201 for registrar_etapa)
    assert r2.status_code == 201, r2.text

    db_session.expire_all()

    # Only ONE E11 row for DTDIS
    rows = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proc["id"],
            EtapaRegistro.codigo_etapa == "E11",
            EtapaRegistro.area_usuaria == "DTDIS",
        )
    ).scalars().all()
    assert len(rows) == 1, f"Expected 1 row, found {len(rows)}: {rows}"

    # Second call must have updated monto_cert
    assert rows[0].monto_cert == Decimal("9999.00"), (
        f"Expected monto_cert=9999.00, got {rows[0].monto_cert}"
    )


# ---------------------------------------------------------------------------
# E11 different areas → two separate rows (NOT upserted together)
# ---------------------------------------------------------------------------

def test_e11_different_areas_create_separate_rows(client, editor_headers, db_session):
    """Posting E11 for two different areas must produce two distinct rows."""
    proc = _create_proceso(client, editor_headers, areas=["DTDIS", "GOBERNANZA"])
    _setup_chain_up_to(db_session, proc["id"], "E11")

    r1 = _post_etapa(
        client, editor_headers, proc["id"], "E11",
        area_usuaria="DTDIS",
        monto_cert="3000.00",
    )
    assert r1.status_code == 201, r1.text

    r2 = _post_etapa(
        client, editor_headers, proc["id"], "E11",
        area_usuaria="GOBERNANZA",
        monto_cert="7000.00",
    )
    assert r2.status_code == 201, r2.text

    db_session.expire_all()

    rows = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proc["id"],
            EtapaRegistro.codigo_etapa == "E11",
        )
    ).scalars().all()
    assert len(rows) == 2, f"Expected 2 rows (one per area), got {len(rows)}"
    areas_found = {r.area_usuaria for r in rows}
    assert areas_found == {"DTDIS", "GOBERNANZA"}


# ---------------------------------------------------------------------------
# E11 upsert preserves monto_cert_total accuracy via E12 trigger
# ---------------------------------------------------------------------------

def test_e11_upsert_then_e12_correct_total(client, editor_headers, db_session):
    """After upsert of E11, E12 monto_cert_total must reflect the updated value.

    Without the fix, duplicates would inflate the SUM.
    """
    from app.models.montos import MontosProceso

    proc = _create_proceso(client, editor_headers, areas=["DTDIS"])
    _setup_chain_up_to(db_session, proc["id"], "E11")

    # First E11
    _post_etapa(
        client, editor_headers, proc["id"], "E11",
        area_usuaria="DTDIS",
        monto_cert="5000.00",
    )
    # Second E11 (upsert — corrects the amount)
    _post_etapa(
        client, editor_headers, proc["id"], "E11",
        area_usuaria="DTDIS",
        monto_cert="8000.00",
    )

    # Now POST E12 which sums all E11 rows
    r_e12 = _post_etapa(client, editor_headers, proc["id"], "E12")
    assert r_e12.status_code == 201, r_e12.text

    db_session.expire_all()
    montos = db_session.execute(
        select(MontosProceso).where(MontosProceso.proceso_id == proc["id"])
    ).scalars().first()
    assert montos is not None
    # Must be 8000, NOT 5000+8000=13000 (the old buggy behaviour)
    assert montos.monto_cert_total == Decimal("8000.00"), (
        f"Expected 8000.00, got {montos.monto_cert_total} — upsert may not have worked"
    )


# ---------------------------------------------------------------------------
# Simple stage (E03) — must still INSERT; upsert must not interfere
# ---------------------------------------------------------------------------

def test_simple_stage_still_inserts(client, editor_headers, db_session):
    """E03 (simple, not por_area) must always INSERT a new row on each POST."""
    proc = _create_proceso(client, editor_headers)
    _setup_chain_up_to(db_session, proc["id"], "E03")

    r = _post_etapa(client, editor_headers, proc["id"], "E03")
    assert r.status_code == 201, r.text

    db_session.expire_all()
    rows = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proc["id"],
            EtapaRegistro.codigo_etapa == "E03",
        )
    ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# E01 upsert (also por_area) — double-submit guard
# ---------------------------------------------------------------------------

def test_e01_double_post_same_area_no_duplicate(client, editor_headers, db_session):
    """E01 is created by _create_proceso; a second POST for same area must upsert."""
    proc = _create_proceso(client, editor_headers, areas=["DTDIS"])

    # E01 row already exists from proceso creation — post again for same area
    r = _post_etapa(
        client, editor_headers, proc["id"], "E01",
        area_usuaria="DTDIS",
        cmn_adjunto="ACTUALIZADO",
    )
    assert r.status_code == 201, r.text

    db_session.expire_all()
    rows = db_session.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proc["id"],
            EtapaRegistro.codigo_etapa == "E01",
            EtapaRegistro.area_usuaria == "DTDIS",
        )
    ).scalars().all()
    assert len(rows) == 1, f"Expected 1 E01 row for DTDIS, got {len(rows)}"
    assert rows[0].cmn_adjunto == "ACTUALIZADO"
