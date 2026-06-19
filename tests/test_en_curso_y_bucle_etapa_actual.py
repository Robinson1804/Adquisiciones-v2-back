"""Tests para los dos bugs corregidos en la estandarización de estados.

Bug #1: 'EN CURSO' (espacio) vs 'EN_CURSO' (guión bajo)
  (a) POST con estado_etapa='EN_CURSO' persiste y NO viola el CHECK constraint.
  (b) El valor nativo en BD es 'EN_CURSO' — no existe 'EN CURSO' en el código de app.
  (c) GET agrupa y devuelve 'EN_CURSO' sin normalización extra.

Bug #3: etapa_actual cae en bucles opcionales vacíos
  (d) calcular_progreso NO devuelve un cod es_bucle como etapa_actual aunque esté
      vacío (PENDIENTE) y haya etapas posteriores no-bucle completadas o pendientes.
  (e) Escenario real E2E: proceso con E01-E07 COMPLETADO + E08a/E08b vacíos
      + E08..E25 con algunas COMPLETADO → etapa_actual es la primera no-bucle
      pendiente, nunca E08a ni E08b.

Usa los fixtures de conftest: client, editor_headers, db_session.
autouse _clean_business_tables garantiza aislamiento entre tests.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select, text

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso
from app.schemas.etapa import EtapaCreate
from app.services.etapas_catalogo import ETAPAS_CATALOGO, ORDEN_ETAPAS
from app.services.etapas_service import calcular_progreso, registrar_etapa


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_proceso(client, headers) -> dict:
    payload = {
        "requerimiento": "Test EN_CURSO y bucle etapa_actual",
        "tipo": "SERVICIO",
        "areas_usuarias": ["DTDIS"],
        "anno": 2026,
        "cmn_por_area": [{"area": "DTDIS", "cmn_adjunto": "SI"}],
    }
    resp = client.post("/procesos", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_proceso_direct(db_session) -> Proceso:
    p = Proceso(
        id_proceso="2026-EN-CURSO-TST",
        requerimiento="Test EN_CURSO directo",
        tipo="SERVICIO",
        areas_usuarias=["DTDIS"],
        estado="EN PROCESO",
        anno=2026,
        creado_por="testuser",
    )
    db_session.add(p)
    db_session.flush()
    return p


def _insert_row(
    db_session,
    proceso_id: int,
    cod: str,
    estado: str = "COMPLETADO",
    es_bucle: bool | None = None,
    nro_ronda: int = 1,
    **kwargs,
) -> EtapaRegistro:
    spec = ETAPAS_CATALOGO.get(cod)
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=spec.nombre if spec else cod,
        area_responsable=spec.area_responsable if spec else "OTIN",
        estado_etapa=estado,
        nro_ronda=nro_ronda,
        es_bucle=(spec.es_bucle if spec else False) if es_bucle is None else es_bucle,
        registrado_por="testsetup",
        **kwargs,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_row(cod: str, estado: str, es_bucle: bool = False, nro_ronda: int = 1) -> EtapaRegistro:
    """Crea un EtapaRegistro en memoria (sin persistir) para unit tests puros."""
    r = EtapaRegistro()
    r.codigo_etapa = cod
    r.estado_etapa = estado
    r.nro_ronda = nro_ronda
    r.es_bucle = es_bucle
    return r


# ===========================================================================
# Bug #1: estandarización EN_CURSO
# ===========================================================================

class TestEnCursoEstandarizado:
    """Bug #1: POST con EN_CURSO no viola CHECK constraint."""

    def test_post_en_curso_no_viola_constraint(self, client, editor_headers, db_session):
        """(a) POST /procesos/{id}/etapas con estado_etapa='EN_CURSO' → 201.

        flujo-real-otin-v2: prereq chain for E02 is E01a→E01b→E01c.
        """
        proc = _create_proceso(client, editor_headers)
        pid = proc["id"]

        # flujo-real-otin-v2: insert E01a/E01b/E01c COMPLETADO to satisfy prereq for E02
        for cod, kw in [("E01a", {}), ("E01b", {}), ("E01c", {"area_usuaria": "DTDIS"})]:
            _insert_row(db_session, pid, cod, estado="COMPLETADO", **kw)

        resp = client.post(
            f"/procesos/{pid}/etapas",
            json={
                "codigo_etapa": "E02",
                "nombre_etapa": "Elaboración TDR",
                "fecha_inicio": "2026-06-01",
                "estado_etapa": "EN_CURSO",
            },
            headers=editor_headers,
        )
        assert resp.status_code == 201, (
            f"POST con EN_CURSO devolvió {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["estado_etapa"] == "EN_CURSO"

    def test_en_curso_persiste_en_bd(self, db_session):
        """(a) El valor 'EN_CURSO' se puede guardar en BD sin violar el CHECK constraint."""
        p = _make_proceso_direct(db_session)
        row = EtapaRegistro(
            proceso_id=p.id,
            codigo_etapa="E03",
            nombre_etapa="Envío indagación",
            area_responsable="OTIN",
            estado_etapa="EN_CURSO",
            nro_ronda=1,
            es_bucle=False,
            registrado_por="testsetup",
        )
        db_session.add(row)
        # No debe lanzar IntegrityError / CheckViolation
        db_session.flush()
        db_session.refresh(row)
        assert row.estado_etapa == "EN_CURSO"

    def test_en_curso_con_espacio_no_aceptado_por_bd(self, db_session):
        """(b) El valor 'EN CURSO' con espacio ya NO es válido en BD (CHECK constraint)."""
        from sqlalchemy.exc import IntegrityError
        p = _make_proceso_direct(db_session)
        row = EtapaRegistro(
            proceso_id=p.id,
            codigo_etapa="E04",
            nombre_etapa="Publicación TDR",
            area_responsable="OTIN",
            estado_etapa="EN CURSO",  # con espacio — debe violar el constraint
            nro_ronda=1,
            es_bucle=False,
            registrado_por="testsetup",
        )
        db_session.add(row)
        with pytest.raises(Exception):  # IntegrityError o CheckViolation
            db_session.flush()
        db_session.rollback()

    def test_get_etapas_devuelve_en_curso_con_guion(self, client, editor_headers, db_session):
        """(c) GET /procesos/{id}/etapas devuelve estado='EN_CURSO' (sin normalización adicional)."""
        proc = _create_proceso(client, editor_headers)
        pid = proc["id"]

        # Insertar E02 directamente con EN_CURSO (el valor nativo)
        _insert_row(db_session, pid, "E02", estado="EN_CURSO")

        resp = client.get(f"/procesos/{pid}/etapas", headers=editor_headers)
        assert resp.status_code == 200, resp.text
        etapas = resp.json()["etapas"]
        e02 = next(e for e in etapas if e["cod"] == "E02")
        assert e02["estado"] == "EN_CURSO", (
            f"Esperaba 'EN_CURSO', obtuve '{e02['estado']}'"
        )

    def test_service_registrar_etapa_en_curso(self, db_session):
        """(a) Service layer: registrar_etapa con EN_CURSO persiste correctamente.

        flujo-real-otin-v2: E01a/E01b/E01c needed as prereqs for E02.
        """
        p = _make_proceso_direct(db_session)
        # flujo-real-otin-v2: insert E01a/E01b/E01c COMPLETADO
        _insert_row(db_session, p.id, "E01a", estado="COMPLETADO")
        _insert_row(db_session, p.id, "E01b", estado="COMPLETADO")
        _insert_row(db_session, p.id, "E01c", estado="COMPLETADO", area_usuaria="DTDIS")

        payload = EtapaCreate(
            codigo_etapa="E02",
            nombre_etapa="Elaboración TDR",
            estado_etapa="EN_CURSO",
        )
        etapa = registrar_etapa(db_session, p.id, payload, "testuser")
        assert etapa.estado_etapa == "EN_CURSO"

    def test_no_existe_en_curso_con_espacio_en_check_constraint(self, db_session):
        """(b) El CHECK constraint de la BD solo acepta 'EN_CURSO' (guión bajo), no 'EN CURSO'."""
        result = db_session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_etapas_estado'"
            )
        ).scalar_one()
        assert "EN_CURSO" in result, f"'EN_CURSO' no encontrado en constraint: {result}"
        assert "EN CURSO" not in result, (
            f"'EN CURSO' con espacio aún está en el constraint (bug #1 no arreglado): {result}"
        )


# ===========================================================================
# Bug #3: etapa_actual no debe ser un bucle
# ===========================================================================

class TestEtapaActualNoBucle:
    """Bug #3: calcular_progreso no elige etapas es_bucle como etapa_actual."""

    def test_etapa_actual_no_es_bucle_vacio(self):
        """(d) Con E08a/E08b vacíos (PENDIENTE implícito), etapa_actual NO es E08a.

        flujo-real-otin-v2: chain starts at E01a; use new codes.
        """
        # Simula proceso con E01a/E01b/E01c/E02/E02b/E03/E04/E07 COMPLETADO
        rows: list[EtapaRegistro] = []
        for cod in ["E01a", "E01b", "E01c", "E02", "E02b", "E03", "E04", "E07"]:
            rows.append(_make_row(cod, "COMPLETADO", es_bucle=False))

        progreso = calcular_progreso(rows)

        # E05/E06/E06b/E06c/E08a/E08b son bucles y no deben ser etapa_actual
        bucle_cods = {cod for cod, spec in ETAPAS_CATALOGO.items() if spec.es_bucle}
        assert progreso.etapa_actual not in bucle_cods, (
            f"etapa_actual={progreso.etapa_actual!r} es un bucle — Bug #3 no arreglado"
        )

    def test_etapa_actual_salta_e08a_e08b_vacios(self):
        """(d) E08a/E08b vacíos → etapa_actual debe ser E08 (primera no-bucle pendiente).

        flujo-real-otin-v2: chain starts at E01a.
        """
        rows: list[EtapaRegistro] = []
        # E01a/E01b/E01c/E02/E02b/E03/E04/E07 COMPLETADO (no-bucle)
        for cod in ["E01a", "E01b", "E01c", "E02", "E02b", "E03", "E04", "E07"]:
            rows.append(_make_row(cod, "COMPLETADO", es_bucle=False))
        # E05/E06/E06b/E06c sin filas → bucles → ignorados
        # E08a/E08b sin filas → bucles → ignorados
        # → primera no-bucle pendiente es E08

        progreso = calcular_progreso(rows)
        assert progreso.etapa_actual == "E08", (
            f"Esperaba E08 como etapa_actual, obtuve '{progreso.etapa_actual}'. "
            "Los bucles vacíos no deben ser etapa_actual."
        )

    def test_etapa_actual_salta_bucles_con_ronda_pendiente(self):
        """(d) Un bucle con ronda en PENDIENTE tampoco debe ser etapa_actual.

        flujo-real-otin-v2: chain starts at E01a.
        """
        rows: list[EtapaRegistro] = []
        # E01a/E01b/E01c/E02/E02b/E03/E04 COMPLETADO
        for cod in ["E01a", "E01b", "E01c", "E02", "E02b", "E03", "E04"]:
            rows.append(_make_row(cod, "COMPLETADO", es_bucle=False))
        # E05 bucle con ronda PENDIENTE — NO debe ser etapa_actual
        rows.append(_make_row("E05", "PENDIENTE", es_bucle=True, nro_ronda=1))

        progreso = calcular_progreso(rows)
        # etapa_actual debe ser E07 (primer no-bucle no-COMPLETADO después de E04)
        assert progreso.etapa_actual not in {"E05", "E06", "E06b", "E06c", "E08a", "E08b"}, (
            f"etapa_actual={progreso.etapa_actual!r} es un bucle — Bug #3"
        )
        assert progreso.etapa_actual == "E07", (
            f"Esperaba E07, obtuve '{progreso.etapa_actual}'"
        )

    def test_etapa_actual_escenario_e2e_hasta_e25(self, db_session):
        """(e) Proceso real: E01a/b/c/E02/E02b/E03-E07 + E08 + algunos hasta E18 COMPLETADO.

        Verifica que etapa_actual sea la primera etapa NO-bucle no completada,
        nunca E08a ni E08b.
        flujo-real-otin-v2: chain starts at E01a.
        """
        p = _make_proceso_direct(db_session)

        # flujo-real-otin-v2 chain head: E01a/E01b/E01c COMPLETADO
        _insert_row(db_session, p.id, "E01a", estado="COMPLETADO")
        _insert_row(db_session, p.id, "E01b", estado="COMPLETADO")
        _insert_row(db_session, p.id, "E01c", estado="COMPLETADO", area_usuaria="DTDIS")

        # E02/E02b/E03/E04/E07, E08 COMPLETADO; E08a/E08b sin filas (bucles opcionales)
        for cod in ["E02", "E02b", "E03", "E04", "E07", "E08"]:
            _insert_row(db_session, p.id, cod, estado="COMPLETADO")

        # E09-E18 COMPLETADO
        for cod in ["E09", "E10", "E11", "E12", "E13", "E14", "E15", "E16", "E17", "E18"]:
            _insert_row(db_session, p.id, cod, estado="COMPLETADO")

        # E19-E25 PENDIENTE/sin fila → E19 es la siguiente no-bucle pendiente
        rows = db_session.execute(
            select(EtapaRegistro).where(EtapaRegistro.proceso_id == p.id)
        ).scalars().all()

        progreso = calcular_progreso(list(rows))

        bucle_cods = {cod for cod, spec in ETAPAS_CATALOGO.items() if spec.es_bucle}
        assert progreso.etapa_actual not in bucle_cods, (
            f"etapa_actual={progreso.etapa_actual!r} es un bucle — Bug #3"
        )
        # La primera no-bucle pendiente debe ser E19
        assert progreso.etapa_actual == "E19", (
            f"Esperaba E19, obtuve '{progreso.etapa_actual}'"
        )

    def test_etapa_actual_proceso_culminado_many_completed(self):
        """(e) Todos los no-bucle COMPLETADO → etapa_actual=None (proceso terminado)."""
        rows: list[EtapaRegistro] = []
        for cod in ORDEN_ETAPAS:
            spec = ETAPAS_CATALOGO[cod]
            if not spec.es_bucle:
                rows.append(_make_row(cod, "COMPLETADO", es_bucle=False))

        progreso = calcular_progreso(rows)
        # Todos los no-bucle completados → etapa_actual debe ser None
        assert progreso.etapa_actual is None, (
            f"Esperaba etapa_actual=None, obtuve '{progreso.etapa_actual}'"
        )
        # completadas == número de non-bucle stages
        non_bucle_count = sum(1 for cod in ORDEN_ETAPAS if not ETAPAS_CATALOGO[cod].es_bucle)
        assert progreso.completadas == non_bucle_count
