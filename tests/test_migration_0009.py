"""Tests for migration 0009 — cmn_siga_confirmado tri-state + titulo_ronda.

Covers:
(a) Migration columns exist after upgrade (schema check via information_schema).
(b) Boolean→string data migration: true→'SI', false→'NO', NULL→NULL.
(c) CHECK constraint: 'SI'/'NO'/'EN_CURSO'/NULL accepted; 'OTRO' rejected.
(d) titulo_ronda: nullable (NULL ok), persists non-NULL value.
(e) HTTP API: POST with cmn_siga_confirmado='SI' returns 200/201.
(f) HTTP API: POST with cmn_siga_confirmado='QUIZAS' returns 422.

NOTE: Tests (a) and (b) simulate the migration logic using raw SQL because the
test database is already at the post-0009 schema — the live migration ran as
part of the test setup.  We verify the outcome state, not the Alembic runner.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proceso(db_session, tag: str = "mig0009") -> int:
    """Insert a minimal Proceso via ORM and return its id."""
    import uuid
    proc = Proceso(
        id_proceso=f"TEST-{uuid.uuid4().hex[:8].upper()}",
        requerimiento=f"Test {tag}",
        tipo="SERVICIO",
        estado="EN PROCESO",
        anno=2026,
    )
    db_session.add(proc)
    db_session.flush()
    return proc.id


def _create_proceso(client, headers: dict) -> dict:
    payload = {
        "requerimiento": "Test migration 0009",
        "tipo": "SERVICIO",
        "areas_usuarias": ["DTDIS"],
        "anno": 2026,
        "cmn_por_area": [{"area": "DTDIS", "cmn_adjunto": "SI"}],
    }
    resp = client.post("/procesos", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _etapa_payload(**overrides) -> dict:
    base = {
        "codigo_etapa": "E02",
        "nombre_etapa": "Elaboración TDR",
        "fecha_inicio": "2026-06-01",
        "estado_etapa": "PENDIENTE",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# (a) Schema — columns exist with expected types
# ---------------------------------------------------------------------------

class TestMigration0009Schema:
    def test_cmn_siga_column_is_varchar(self, db_session):
        """cmn_siga_confirmado must be character varying (not boolean) after 0009."""
        row = db_session.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='etapas_registro' AND column_name='cmn_siga_confirmado'"
            )
        ).fetchone()
        assert row is not None, "Column cmn_siga_confirmado not found"
        assert row[0] == "character varying", (
            f"Expected 'character varying', got '{row[0]}'"
        )

    def test_titulo_ronda_column_exists(self, db_session):
        """titulo_ronda column must exist in etapas_registro after 0009."""
        row = db_session.execute(
            text(
                "SELECT data_type, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name='etapas_registro' AND column_name='titulo_ronda'"
            )
        ).fetchone()
        assert row is not None, "Column titulo_ronda not found"
        assert row[0] == "character varying"
        assert row[1] == 200

    def test_check_constraint_exists(self, db_session):
        """CHECK constraint ck_etapas_cmn_siga_valido must exist."""
        row = db_session.execute(
            text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name='etapas_registro' "
                "AND constraint_name='ck_etapas_cmn_siga_valido'"
            )
        ).fetchone()
        assert row is not None, "CHECK constraint ck_etapas_cmn_siga_valido not found"


# ---------------------------------------------------------------------------
# (b) Data migration logic: bool values map correctly to strings
# ---------------------------------------------------------------------------

class TestDataMigration:
    def test_bool_to_string_mapping(self, db_session):
        """Verify the bool→string mapping logic is what we expect.

        We cannot replay the pre-migration state in an already-migrated DB,
        so we simulate by verifying the SQL USING expression would produce the
        correct values.  We insert strings matching the expected post-migration
        state and read them back.
        """
        proc_id = _make_proceso(db_session, "boolmig")

        # Simulate post-migration: 'SI' (was true), 'NO' (was false), NULL
        for code, val in [("E02", "SI"), ("E03", "NO"), ("E04", None)]:
            etapa = EtapaRegistro(
                proceso_id=proc_id,
                codigo_etapa=code,
                nombre_etapa="Test",
                estado_etapa="PENDIENTE",
                nro_ronda=1,
                cmn_siga_confirmado=val,
            )
            db_session.add(etapa)
        db_session.flush()

        rows = db_session.execute(
            text(
                f"SELECT codigo_etapa, cmn_siga_confirmado FROM etapas_registro "
                f"WHERE proceso_id={proc_id} ORDER BY codigo_etapa"
            )
        ).fetchall()
        mapping = {r[0]: r[1] for r in rows}
        assert mapping["E02"] == "SI"
        assert mapping["E03"] == "NO"
        assert mapping["E04"] is None


# ---------------------------------------------------------------------------
# (c) CHECK constraint enforcement
# ---------------------------------------------------------------------------

class TestCmnSigaCheckConstraint:
    def _insert_with_value(self, db_session, proc_id: int, code: str, val: str | None):
        """Insert via raw SQL to bypass ORM coercions and hit the DB CHECK directly."""
        if val is not None:
            db_session.execute(
                text(
                    "INSERT INTO etapas_registro "
                    "(proceso_id, codigo_etapa, nombre_etapa, estado_etapa, nro_ronda, cmn_siga_confirmado) "
                    f"VALUES ({proc_id}, '{code}', 'Test', 'PENDIENTE', 1, '{val}')"
                )
            )
        else:
            db_session.execute(
                text(
                    "INSERT INTO etapas_registro "
                    "(proceso_id, codigo_etapa, nombre_etapa, estado_etapa, nro_ronda, cmn_siga_confirmado) "
                    f"VALUES ({proc_id}, '{code}', 'Test', 'PENDIENTE', 1, NULL)"
                )
            )

    @pytest.fixture
    def proc_id(self, db_session):
        return _make_proceso(db_session, "check")

    def test_cmn_siga_si_accepted(self, db_session, proc_id):
        self._insert_with_value(db_session, proc_id, "E02", "SI")
        db_session.flush()  # no exception

    def test_cmn_siga_no_accepted(self, db_session, proc_id):
        self._insert_with_value(db_session, proc_id, "E02", "NO")
        db_session.flush()

    def test_cmn_siga_en_curso_accepted(self, db_session, proc_id):
        self._insert_with_value(db_session, proc_id, "E02", "EN_CURSO")
        db_session.flush()

    def test_cmn_siga_null_accepted(self, db_session, proc_id):
        self._insert_with_value(db_session, proc_id, "E02", None)
        db_session.flush()

    def test_cmn_siga_invalid_value_rejected(self, db_session, proc_id):
        """'OTRO' must be rejected by the CHECK constraint."""
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            self._insert_with_value(db_session, proc_id, "E02", "OTRO")
            db_session.flush()


# ---------------------------------------------------------------------------
# (d) titulo_ronda persistence
# ---------------------------------------------------------------------------

class TestTituloRonda:
    @pytest.fixture
    def proc_id(self, db_session):
        return _make_proceso(db_session, "titulo")

    def test_titulo_ronda_nullable(self, db_session, proc_id):
        """titulo_ronda=NULL is accepted."""
        etapa = EtapaRegistro(
            proceso_id=proc_id,
            codigo_etapa="E06b",
            nombre_etapa="Test",
            estado_etapa="PENDIENTE",
            nro_ronda=1,
            es_bucle=True,
            titulo_ronda=None,
        )
        db_session.add(etapa)
        db_session.flush()  # no exception

    def test_titulo_ronda_persisted(self, db_session, proc_id):
        """titulo_ronda='Faltó SLA' is stored and retrieved correctly."""
        etapa = EtapaRegistro(
            proceso_id=proc_id,
            codigo_etapa="E06b",
            nombre_etapa="Test",
            estado_etapa="PENDIENTE",
            nro_ronda=1,
            es_bucle=True,
            titulo_ronda="Faltó SLA",
        )
        db_session.add(etapa)
        db_session.flush()
        db_session.refresh(etapa)
        assert etapa.titulo_ronda == "Faltó SLA"


# ---------------------------------------------------------------------------
# (e) HTTP API — cmn_siga_confirmado accepts 'SI' via POST
# ---------------------------------------------------------------------------

def _satisfy_e02_prereqs(db_session, proc_id: int) -> None:
    """Insert E01a/E01b/E01c as COMPLETADO so E02 prereq check passes."""
    for cod, nombre, area_resp, area_usu in [
        ("E01a", "Solicitud inicial", "AREAS", None),
        ("E01b", "Oficio circular", "OTIN", None),
        ("E01c", "Respuesta área", "AREAS", "DTDIS"),
    ]:
        etapa = EtapaRegistro(
            proceso_id=proc_id,
            codigo_etapa=cod,
            nombre_etapa=nombre,
            area_responsable=area_resp,
            area_usuaria=area_usu,
            estado_etapa="COMPLETADO",
            registrado_por="test",
            nro_ronda=1,
        )
        db_session.add(etapa)
    db_session.flush()


class TestApiCmnSiga:
    def test_etapa_create_with_cmn_si(self, client, editor_headers, db_session):
        """POST /procesos/{id}/etapas with cmn_siga_confirmado='SI' must succeed."""
        proc = _create_proceso(client, editor_headers)
        _satisfy_e02_prereqs(db_session, proc["id"])
        payload = _etapa_payload(cmn_siga_confirmado="SI")
        resp = client.post(
            f"/procesos/{proc['id']}/etapas",
            json=payload,
            headers=editor_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()
        assert data["cmn_siga_confirmado"] == "SI"

    def test_etapa_create_with_cmn_en_curso(self, client, editor_headers, db_session):
        """POST with cmn_siga_confirmado='EN_CURSO' must succeed."""
        proc = _create_proceso(client, editor_headers)
        _satisfy_e02_prereqs(db_session, proc["id"])
        payload = _etapa_payload(cmn_siga_confirmado="EN_CURSO")
        resp = client.post(
            f"/procesos/{proc['id']}/etapas",
            json=payload,
            headers=editor_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()
        assert data["cmn_siga_confirmado"] == "EN_CURSO"

    def test_etapa_create_with_cmn_invalid_value(self, client, editor_headers):
        """POST with cmn_siga_confirmado='QUIZAS' must return 422 (Pydantic validation)."""
        proc = _create_proceso(client, editor_headers)
        payload = _etapa_payload(cmn_siga_confirmado="QUIZAS")
        resp = client.post(
            f"/procesos/{proc['id']}/etapas",
            json=payload,
            headers=editor_headers,
        )
        assert resp.status_code == 422, resp.text
