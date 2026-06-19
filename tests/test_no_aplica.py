"""Tests for NO_APLICA estado and E06b catalog additions.

Covers:
(a) Registering E19 with E09-E18 marked NO_APLICA does not return 409.
(b) Registering a stage as NO_APLICA skips R7 / prereq validation.
(c) Progress excludes NO_APLICA from denominator; etapa_actual skips NO_APLICA.
(d) E06b exists in catalog as a bucle and does NOT add a prerequisite to E07.
(e) E06/E08/E20/E22 are now in CODIGOS_CON_ADJUNTOS.
(f) Migration integrity: NO_APLICA is a valid DB estado_etapa value.

Uses conftest fixtures: client, editor_headers, db_session.
autouse _clean_business_tables ensures isolation.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select, text

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso
from app.services.etapas_catalogo import (
    CADENA,
    CODIGOS_CON_ADJUNTOS,
    ETAPAS_CATALOGO,
    ORDEN_ETAPAS,
    PROGRESO_DENOMINATOR,
)
from app.services.etapas_service import (
    calcular_progreso,
    registrar_etapa,
)
from app.schemas.etapa import EtapaCreate


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers, areas=None) -> dict:
    payload = {
        "requerimiento": "Test NO_APLICA",
        "tipo": "SERVICIO",
        "areas_usuarias": areas or ["DTDIS"],
        "anno": 2026,
        "cmn_por_area": [{"area": a, "cmn_adjunto": "SI"} for a in (areas or ["DTDIS"])],
    }
    resp = client.post("/procesos", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_proceso_direct(db_session) -> Proceso:
    p = Proceso(
        id_proceso="2026-NA-TST",
        requerimiento="Test NO_APLICA direct",
        tipo="SERVICIO",
        areas_usuarias=["DTDIS"],
        estado="EN PROCESO",
        anno=2026,
        creado_por="testuser",
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
        nro_ronda=kwargs.pop("nro_ronda", 1),
        es_bucle=spec.es_bucle if spec else False,
        registrado_por="testsetup",
        **kwargs,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _post_etapa(client, headers, proceso_id, cod, estado="COMPLETADO", **extra):
    payload = {
        "codigo_etapa": cod,
        "nombre_etapa": f"Etapa {cod}",
        "fecha_inicio": "2026-06-01",
        "estado_etapa": estado,
    }
    payload.update(extra)
    return client.post(
        f"/procesos/{proceso_id}/etapas",
        json=payload,
        headers=headers,
    )


def _make_etapa_obj(
    db_session,
    proceso_id: int,
    cod: str,
    estado: str = "COMPLETADO",
    **kwargs,
) -> EtapaRegistro:
    """Insert a raw EtapaRegistro (no spec lookup, for unit tests)."""
    r = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=f"Etapa {cod}",
        area_responsable="OTIN",
        estado_etapa=estado,
        nro_ronda=1,
        es_bucle=False,
        registrado_por="testuser",
        **kwargs,
    )
    db_session.add(r)
    db_session.flush()
    return r


# ---------------------------------------------------------------------------
# (a) E19 with E09-E18 marked NO_APLICA → should NOT return 409
# ---------------------------------------------------------------------------

def test_e19_con_e09_e18_no_aplica_no_da_409(client, editor_headers, db_session):
    """POST E19 succeeds when E09-E18 are all marked NO_APLICA (orden de servicio directo)."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    # Set up E01-E08 chain (E08 needs APROBADO for R7, but E09 will be NO_APLICA skip)
    from tests.test_validaciones import _set_e01_completado
    _set_e01_completado(db_session, pid)
    for cod in ["E02", "E03", "E04", "E07"]:
        _insert_etapa(db_session, pid, cod)
    _insert_etapa(db_session, pid, "E08", resultado_eval="APROBADO")

    # Mark E09-E18 as NO_APLICA (direct service order skips budget certification)
    for cod in ["E09", "E10", "E11", "E12", "E13", "E14", "E15", "E16", "E17", "E18"]:
        resp = _post_etapa(client, editor_headers, pid, cod, estado="NO_APLICA")
        assert resp.status_code == 201, f"{cod} → {resp.status_code}: {resp.text}"

    # Now register E19 — should succeed (E18 is NO_APLICA, satisfies prereq)
    resp = _post_etapa(
        client, editor_headers, pid, "E19",
        estado="COMPLETADO",
        nro_ocs="OCS-2026-001",
        monto_ocs="50000.00",
        plazo_entrega=30,
    )
    assert resp.status_code == 201, f"E19 blocked: {resp.text}"


# ---------------------------------------------------------------------------
# (b) NO_APLICA skips prereq validation and stage-specific rules (R7)
# ---------------------------------------------------------------------------

def test_no_aplica_skips_prereq(client, editor_headers, db_session):
    """Registrar E09 como NO_APLICA no requiere que E08 esté COMPLETADO."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    # E08 does NOT exist at all — normally registering E09 would fail prereq check
    # But with NO_APLICA, prereq is skipped entirely
    resp = _post_etapa(client, editor_headers, pid, "E09", estado="NO_APLICA")
    assert resp.status_code == 201, f"E09 NO_APLICA blocked: {resp.text}"


def test_no_aplica_skips_r7(client, editor_headers, db_session):
    """Registrar E09 como NO_APLICA no requiere E08.resultado_eval='APROBADO' (R7 skipped)."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    # Insert E08 with non-APROBADO resultado — normally would block E09 via R7
    _insert_etapa(db_session, pid, "E08", estado="COMPLETADO", resultado_eval="OBSERVADO")

    # NO_APLICA bypasses R7
    resp = _post_etapa(client, editor_headers, pid, "E09", estado="NO_APLICA")
    assert resp.status_code == 201, f"E09 NO_APLICA with bad R7 blocked: {resp.text}"


def test_no_aplica_proceso_cancelado_still_blocks(client, editor_headers, db_session):
    """NO_APLICA does NOT bypass the proceso-CANCELADO gate."""
    from tests.test_validaciones import _setup_chain_prereqs
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    # Cancel the proceso via E10 SIN_PRESUPUESTO
    _setup_chain_prereqs(db_session, pid, "E10")
    _post_etapa(
        client, editor_headers, pid, "E10",
        resultado_eval="SIN_PRESUPUESTO",
        motivo_cancel="Sin presupuesto para test",
    )

    # Attempt NO_APLICA on a stage — should still be blocked (proceso is CANCELADO)
    resp = _post_etapa(client, editor_headers, pid, "E15", estado="NO_APLICA")
    assert resp.status_code == 409, f"Expected 409 for CANCELADO, got {resp.status_code}"
    assert "cancelado" in resp.json()["detail"].lower()


def test_no_aplica_satisfies_prereq_for_next(db_session):
    """A stage marked NO_APLICA satisfies the prerequisite for the next stage.

    Tests validar_prerequisito_generico directly via registrar_etapa service.
    flujo-real-otin-v2: E02 prereq chain is E01a→E01b→E01c. Register E02 as NO_APLICA;
    then E02b (prereq=E02) and E03 (prereq=E02b) should be registerable.
    """
    p = _make_proceso_direct(db_session)

    # flujo-real-otin-v2: E01a/E01b/E01c COMPLETADO (prereq chain for E02)
    _make_etapa_obj(db_session, p.id, "E01a")
    _make_etapa_obj(db_session, p.id, "E01b")
    _make_etapa_obj(db_session, p.id, "E01c", area_usuaria="DTDIS")

    # Register E02 as NO_APLICA (skips prereq for E02 itself)
    payload_e02 = EtapaCreate(
        codigo_etapa="E02",
        nombre_etapa="Elaboración TDR",
        estado_etapa="NO_APLICA",
    )
    e02_row = registrar_etapa(db_session, p.id, payload_e02, "testuser")
    assert e02_row.estado_etapa == "NO_APLICA"

    # Register E02b as NO_APLICA (prereq=E02 is NO_APLICA → satisfied)
    payload_e02b = EtapaCreate(
        codigo_etapa="E02b",
        nombre_etapa="Consolidado CMN",
        estado_etapa="NO_APLICA",
    )
    e02b_row = registrar_etapa(db_session, p.id, payload_e02b, "testuser")
    assert e02b_row.estado_etapa == "NO_APLICA"

    # Now register E03 — E02b is NO_APLICA, prereq should be satisfied
    payload_e03 = EtapaCreate(
        codigo_etapa="E03",
        nombre_etapa="Envío indagación",
        estado_etapa="PENDIENTE",
    )
    e03_row = registrar_etapa(db_session, p.id, payload_e03, "testuser")
    assert e03_row.estado_etapa == "PENDIENTE"


# ---------------------------------------------------------------------------
# (c) Progress: NO_APLICA excluded from denominator; etapa_actual skips NO_APLICA
# ---------------------------------------------------------------------------

def test_progreso_no_aplica_excluye_denominador():
    """Stages marked NO_APLICA reduce the denominator, allowing 100% completion.

    flujo-real-otin-v2: PROGRESO_DENOMINATOR=26 (was 25).
    """
    rows = []

    # Complete E01a (non-bucle, first in new chain)
    r = EtapaRegistro()
    r.codigo_etapa = "E01a"
    r.estado_etapa = "COMPLETADO"
    r.nro_ronda = 1
    r.es_bucle = False
    rows.append(r)

    # Mark E02, E03, E04 as NO_APLICA (3 stages)
    for cod in ["E02", "E03", "E04"]:
        r = EtapaRegistro()
        r.codigo_etapa = cod
        r.estado_etapa = "NO_APLICA"
        r.nro_ronda = 1
        r.es_bucle = False
        rows.append(r)

    progreso = calcular_progreso(rows)

    # flujo-real-otin-v2: Denominator = 26 - 3 = 23
    assert progreso.total == 23
    # Completed = 1 (E01a)
    assert progreso.completadas == 1
    # porcentaje ≈ 1/23 * 100 ≈ 4.3
    expected_pct = round(1 / 23 * 100, 1)
    assert progreso.porcentaje == expected_pct


def test_progreso_no_aplica_etapa_actual_no_es_no_aplica():
    """etapa_actual skips NO_APLICA stages — points to the first genuinely pending stage.

    flujo-real-otin-v2: PROGRESO_DENOMINATOR=26. Chain: E01a→E01b→E01c→E02→E02b→E03.
    """
    rows = []

    # E01a/E01b/E01c COMPLETADO (non-bucle, chain head)
    for cod in ["E01a", "E01b", "E01c"]:
        r = EtapaRegistro()
        r.codigo_etapa = cod
        r.estado_etapa = "COMPLETADO"
        r.nro_ronda = 1
        r.es_bucle = False
        rows.append(r)

    # E02 NO_APLICA
    r = EtapaRegistro()
    r.codigo_etapa = "E02"
    r.estado_etapa = "NO_APLICA"
    r.nro_ronda = 1
    r.es_bucle = False
    rows.append(r)

    progreso = calcular_progreso(rows)

    # E01a/E01b/E01c done, E02 is NO_APLICA (skipped), so etapa_actual = E02b (first pending)
    assert progreso.etapa_actual == "E02b"
    # flujo-real-otin-v2: Denominator = 26 - 1 (E02 NO_APLICA) = 25
    assert progreso.total == 25


def test_progreso_100_con_no_aplica_y_completado():
    """A process can reach 100% when some stages are NO_APLICA.

    There are 26 non-bucle stages (PROGRESO_DENOMINATOR=26). Bucles are
    E05, E06, E06b, E06c, E08a, E08b (es_bucle=True, excluded from denominator).

    For this test: 5 non-bucle NO_APLICA → denominator=26-5=21. Remaining
    non-bucle (21) COMPLETADO → completadas=21 → 100% possible only via
    CULMINADO override since calcular_progreso counts actual COMPLETADO rows.
      denominator = PROGRESO_DENOMINATOR - no_aplica_count = completadas
      e.g., 5 NO_APLICA (non-bucle) → denominator=21 → need 21 COMPLETADO.

    The only way to reach 100% is if PROGRESO_DENOMINATOR-no_aplica_count <= completadas.
    With no_aplica=5: 26-5=21, need 21 COMPLETADO from 21 available → exactly 100%.

    Conclusion: the denominator=26 is set knowing CULMINADO gives the 100% override.
    This test verifies that NO_APLICA correctly reduces the denominator.
    """
    rows = []
    spec_cods_no_bucle = [cod for cod in ORDEN_ETAPAS if not ETAPAS_CATALOGO[cod].es_bucle]

    # Mark 5 non-bucle stages as NO_APLICA
    no_aplica_cods = spec_cods_no_bucle[:5]
    # Mark remaining 18 non-bucle as COMPLETADO
    completado_cods = spec_cods_no_bucle[5:]

    for cod in no_aplica_cods:
        r = EtapaRegistro()
        r.codigo_etapa = cod
        r.estado_etapa = "NO_APLICA"
        r.nro_ronda = 1
        r.es_bucle = False
        rows.append(r)

    for cod in completado_cods:
        r = EtapaRegistro()
        r.codigo_etapa = cod
        r.estado_etapa = "COMPLETADO"
        r.nro_ronda = 1
        r.es_bucle = False
        rows.append(r)

    progreso = calcular_progreso(rows)

    # flujo-real-otin-v2: non-bucle count = 26, 5 NO_APLICA → Denominator = 26 - 5 = 21
    assert progreso.total == 21
    # Completadas = non_bucle_total - 5 NO_APLICA
    non_bucle_total = len(spec_cods_no_bucle)
    assert progreso.completadas == non_bucle_total - 5
    # Porcentaje = completadas/21 * 100
    expected_pct = round((non_bucle_total - 5) / 21 * 100, 1)
    assert progreso.porcentaje == expected_pct


def test_progreso_vacio_sin_no_aplica():
    """No rows → etapa_actual=first stage, total=25, not affected by NO_APLICA logic."""
    progreso = calcular_progreso([])
    assert progreso.total == PROGRESO_DENOMINATOR  # 26
    assert progreso.etapa_actual == ORDEN_ETAPAS[0]
    assert progreso.porcentaje == 0.0


def test_progreso_omitido_no_cuenta_como_no_aplica():
    """OMITIDO does NOT reduce the denominator (only NO_APLICA does).

    flujo-real-otin-v2: PROGRESO_DENOMINATOR=26.
    """
    r = EtapaRegistro()
    r.codigo_etapa = "E02"
    r.estado_etapa = "OMITIDO"
    r.nro_ronda = 1
    r.es_bucle = False

    progreso = calcular_progreso([r])
    # OMITIDO doesn't reduce denominator — still 26 (flujo-real-otin-v2)
    assert progreso.total == 26
    # OMITIDO doesn't count as completado
    assert progreso.completadas == 0


# ---------------------------------------------------------------------------
# (d) E06b catalog: bucle, not in CADENA, does not add prereq to E07
# ---------------------------------------------------------------------------

def test_e06b_exists_in_catalogo():
    """E06b is present in ETAPAS_CATALOGO."""
    assert "E06b" in ETAPAS_CATALOGO


def test_e06b_es_bucle():
    """E06b has es_bucle=True."""
    assert ETAPAS_CATALOGO["E06b"].es_bucle is True


def test_e06b_area_responsable():
    """E06b area_responsable is BUCLE."""
    assert ETAPAS_CATALOGO["E06b"].area_responsable == "BUCLE"


def test_e06b_acepta_adjuntos():
    """E06b accepts file attachments."""
    assert ETAPAS_CATALOGO["E06b"].acepta_adjuntos is True


def test_e06b_not_in_cadena():
    """E06b is NOT in the main CADENA (it's an optional loop, not a required step)."""
    assert "E06b" not in CADENA


def test_e06b_no_agrega_prereq_a_e07():
    """E07's prerequisito comes from CADENA (its predecessor in the main chain), NOT E06b.

    CADENA = (..., E04, E07, E08, ...) — E07's CADENA predecessor is E04.
    E06b is a bucle outside the chain and must not appear in E07.prerequisitos.
    """
    e07_spec = ETAPAS_CATALOGO["E07"]
    # E07 is in CADENA; its immediate CADENA predecessor is E04 (E05/E06/E06b are bucles, outside chain)
    cadena_idx = CADENA.index("E07")
    cadena_predecessor = CADENA[cadena_idx - 1]
    assert cadena_predecessor in e07_spec.prerequisitos, (
        f"E07.prerequisitos {e07_spec.prerequisitos} should contain CADENA predecessor {cadena_predecessor}"
    )
    # E06b must NOT be a prerequisite of E07
    assert "E06b" not in e07_spec.prerequisitos


def test_e06b_orden_entre_e06_y_e07():
    """E06b appears in ORDEN_ETAPAS between E06 and E07."""
    e06_idx = ORDEN_ETAPAS.index("E06")
    e06b_idx = ORDEN_ETAPAS.index("E06b")
    e07_idx = ORDEN_ETAPAS.index("E07")
    assert e06_idx < e06b_idx < e07_idx


def test_e06b_en_cod_a_fase():
    """E06b is mapped to F2 in COD_A_FASE."""
    from app.services.etapas_catalogo import COD_A_FASE
    assert COD_A_FASE.get("E06b") == "F2"


def test_e06b_bucle_registro(client, editor_headers, db_session):
    """POST /procesos/{id}/etapas/E06b/bucle creates a bucle row."""
    proc = _create_proceso(client, editor_headers)
    pid = proc["id"]

    resp = client.post(
        f"/procesos/{pid}/etapas/E06b/bucle",
        json={"motivo_bucle": "Solicitud VB DTDIS primera ronda"},
        headers=editor_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["codigo_etapa"] == "E06b"
    assert body["es_bucle"] is True
    assert body["nro_ronda"] == 1


# ---------------------------------------------------------------------------
# (e) E06/E08/E20/E22 now in CODIGOS_CON_ADJUNTOS
# ---------------------------------------------------------------------------

def test_e06_acepta_adjuntos():
    """E06 now has acepta_adjuntos=True."""
    assert ETAPAS_CATALOGO["E06"].acepta_adjuntos is True
    assert "E06" in CODIGOS_CON_ADJUNTOS


def test_e08_acepta_adjuntos():
    """E08 now has acepta_adjuntos=True."""
    assert ETAPAS_CATALOGO["E08"].acepta_adjuntos is True
    assert "E08" in CODIGOS_CON_ADJUNTOS


def test_e20_acepta_adjuntos():
    """E20 now has acepta_adjuntos=True."""
    assert ETAPAS_CATALOGO["E20"].acepta_adjuntos is True
    assert "E20" in CODIGOS_CON_ADJUNTOS


def test_e22_acepta_adjuntos():
    """E22 now has acepta_adjuntos=True."""
    assert ETAPAS_CATALOGO["E22"].acepta_adjuntos is True
    assert "E22" in CODIGOS_CON_ADJUNTOS


# ---------------------------------------------------------------------------
# (f) Migration: NO_APLICA is a valid DB value for estado_etapa
# ---------------------------------------------------------------------------

def test_no_aplica_es_valor_valido_en_bd(db_session):
    """NO_APLICA can be stored in etapas_registro.estado_etapa without violating the CHECK constraint."""
    p = _make_proceso_direct(db_session)
    # Insert directly bypassing service layer to test the DB constraint
    row = EtapaRegistro(
        proceso_id=p.id,
        codigo_etapa="E03",
        nombre_etapa="Envío indagación",
        area_responsable="OTIN",
        estado_etapa="NO_APLICA",
        nro_ronda=1,
        es_bucle=False,
        registrado_por="testsetup",
    )
    db_session.add(row)
    # Should not raise IntegrityError / CheckViolation
    db_session.flush()
    db_session.refresh(row)
    assert row.estado_etapa == "NO_APLICA"
