"""Tests for migration 0010 — ingesta_correos + ingesta_documentos + procesos.numero_oficio.

Covers:
(a) Schema: ingesta_correos table exists with expected columns.
(b) Schema: ingesta_documentos table exists with expected columns.
(c) Schema: procesos.numero_oficio column exists (VARCHAR(60), nullable).
(d) UNIQUE constraint on ingesta_correos.entry_id (idempotencia dura).
(e) CHECK constraint: estado_revision accepts PENDIENTE/APROBADO/APROBADO_AUTO/RECHAZADO.
(f) CHECK constraint: estado_revision rejects invalid values.
(g) FK: ingesta_documentos.ingesta_correo_id ON DELETE CASCADE.
(h) FK: ingesta_correos.proceso_id ON DELETE SET NULL.
(i) Index idx_ingesta_estado_revision exists.
(j) Index idx_ingesta_proceso_id exists.
(k) Index idx_ingesta_docs_correo exists.
(l) Index idx_procesos_numero_oficio exists.

NOTE: Tests run against the already-migrated test DB. They verify schema
state, not the Alembic runner itself (same approach as test_migration_0009.py).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.proceso import Proceso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proceso(db_session, tag: str = "mig0010") -> int:
    proc = Proceso(
        id_proceso=f"ING-{uuid.uuid4().hex[:8].upper()}",
        requerimiento=f"Proceso test {tag}",
        tipo="SERVICIO",
        estado="EN PROCESO",
        anno=2026,
    )
    db_session.add(proc)
    db_session.flush()
    return proc.id


def _insert_correo(db_session, entry_id: str, proceso_id: int | None = None) -> int:
    """Insert a minimal ingesta_correo row via raw SQL and return its id."""
    proc_val = str(proceso_id) if proceso_id is not None else "NULL"
    db_session.execute(
        text(
            "INSERT INTO ingesta_correos "
            "(entry_id, subject, sender_email, received_at, estado_revision, proceso_id) "
            f"VALUES ('{entry_id}', 'Asunto test', 'test@example.com', now(), 'PENDIENTE', {proc_val})"
        )
    )
    db_session.flush()
    row = db_session.execute(
        text(f"SELECT id FROM ingesta_correos WHERE entry_id='{entry_id}'")
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# (a) ingesta_correos columns
# ---------------------------------------------------------------------------

class TestIngestaCorreosSchema:
    EXPECTED_COLUMNS = [
        "id", "entry_id", "subject", "sender_name", "sender_email",
        "received_at", "body_clean", "nombre_servicio", "nombre_servicio_normalizado",
        "numero_oficio_raw", "numero_oficio", "oss", "siaf", "proveedor", "tipo",
        "fecha_documento", "fecha_recepcion", "estado_revision", "match_confianza",
        "proceso_id", "motivo_rechazo", "revisado_por", "revisado_en", "creado_en",
    ]

    def test_table_exists(self, db_session):
        row = db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='ingesta_correos'"
            )
        ).fetchone()
        assert row is not None, "Table ingesta_correos not found"

    @pytest.mark.parametrize("col", EXPECTED_COLUMNS)
    def test_column_exists(self, db_session, col):
        row = db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_name='ingesta_correos' AND column_name='{col}'"
            )
        ).fetchone()
        assert row is not None, f"Column {col} not found in ingesta_correos"

    def test_entry_id_is_unique(self, db_session):
        row = db_session.execute(
            text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name='ingesta_correos' AND constraint_type='UNIQUE' "
                "AND constraint_name='uq_ingesta_entry_id'"
            )
        ).fetchone()
        assert row is not None, "UNIQUE constraint uq_ingesta_entry_id not found"

    def test_estado_revision_default_pendiente(self, db_session):
        db_session.execute(
            text(
                "INSERT INTO ingesta_correos (entry_id, subject, sender_email, received_at) "
                "VALUES ('ENTRY-DEFAULT-001', 'Asunto', 'a@b.com', now())"
            )
        )
        db_session.flush()
        row = db_session.execute(
            text(
                "SELECT estado_revision FROM ingesta_correos WHERE entry_id='ENTRY-DEFAULT-001'"
            )
        ).fetchone()
        assert row[0] == "PENDIENTE"

    def test_creado_en_has_default(self, db_session):
        db_session.execute(
            text(
                "INSERT INTO ingesta_correos (entry_id, subject, sender_email, received_at) "
                "VALUES ('ENTRY-CREN-001', 'Asunto', 'a@b.com', now())"
            )
        )
        db_session.flush()
        row = db_session.execute(
            text(
                "SELECT creado_en FROM ingesta_correos WHERE entry_id='ENTRY-CREN-001'"
            )
        ).fetchone()
        assert row[0] is not None, "creado_en should have a default (now())"


# ---------------------------------------------------------------------------
# (b) ingesta_documentos columns
# ---------------------------------------------------------------------------

class TestIngestaDocumentosSchema:
    EXPECTED_COLUMNS = [
        "id", "ingesta_correo_id", "nombre_original", "nombre_almacenado",
        "ruta_relativa", "content_type", "tamano_bytes", "tipo_clasificado",
        "confianza", "proceso_id", "creado_en",
    ]

    def test_table_exists(self, db_session):
        row = db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='ingesta_documentos'"
            )
        ).fetchone()
        assert row is not None, "Table ingesta_documentos not found"

    @pytest.mark.parametrize("col", EXPECTED_COLUMNS)
    def test_column_exists(self, db_session, col):
        row = db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_name='ingesta_documentos' AND column_name='{col}'"
            )
        ).fetchone()
        assert row is not None, f"Column {col} not found in ingesta_documentos"


# ---------------------------------------------------------------------------
# (c) procesos.numero_oficio
# ---------------------------------------------------------------------------

class TestProcesoNumeroOficio:
    def test_numero_oficio_column_exists(self, db_session):
        row = db_session.execute(
            text(
                "SELECT data_type, character_maximum_length FROM information_schema.columns "
                "WHERE table_name='procesos' AND column_name='numero_oficio'"
            )
        ).fetchone()
        assert row is not None, "Column numero_oficio not found in procesos"
        assert row[0] == "character varying"
        assert row[1] == 60

    def test_numero_oficio_nullable(self, db_session):
        proc_id = _make_proceso(db_session, "oficio_null")
        # Should be NULL by default
        row = db_session.execute(
            text(f"SELECT numero_oficio FROM procesos WHERE id={proc_id}")
        ).fetchone()
        assert row[0] is None

    def test_numero_oficio_persists_value(self, db_session):
        proc_id = _make_proceso(db_session, "oficio_value")
        db_session.execute(
            text(
                f"UPDATE procesos SET numero_oficio='267-2026-INEI/OTIN' WHERE id={proc_id}"
            )
        )
        db_session.flush()
        row = db_session.execute(
            text(f"SELECT numero_oficio FROM procesos WHERE id={proc_id}")
        ).fetchone()
        assert row[0] == "267-2026-INEI/OTIN"


# ---------------------------------------------------------------------------
# (d) UNIQUE constraint on entry_id (idempotencia dura)
# ---------------------------------------------------------------------------

class TestEntryIdUnique:
    def test_duplicate_entry_id_rejected(self, db_session):
        """Inserting the same entry_id twice must raise IntegrityError."""
        db_session.execute(
            text(
                "INSERT INTO ingesta_correos (entry_id, subject, sender_email, received_at) "
                "VALUES ('ENTRY-DUP-001', 'First', 'a@b.com', now())"
            )
        )
        db_session.flush()
        with pytest.raises(IntegrityError):
            db_session.execute(
                text(
                    "INSERT INTO ingesta_correos (entry_id, subject, sender_email, received_at) "
                    "VALUES ('ENTRY-DUP-001', 'Second', 'b@c.com', now())"
                )
            )
            db_session.flush()

    def test_different_entry_ids_accepted(self, db_session):
        for i in range(3):
            db_session.execute(
                text(
                    f"INSERT INTO ingesta_correos (entry_id, subject, sender_email, received_at) "
                    f"VALUES ('ENTRY-DIFF-00{i}', 'Asunto', 'a@b.com', now())"
                )
            )
        db_session.flush()  # no exception


# ---------------------------------------------------------------------------
# (e/f) CHECK constraint on estado_revision
# ---------------------------------------------------------------------------

class TestEstadoRevisionCheck:
    @pytest.mark.parametrize(
        "estado",
        ["PENDIENTE", "APROBADO", "APROBADO_AUTO", "RECHAZADO"],
    )
    def test_valid_estados_accepted(self, db_session, estado):
        eid = f"ENTRY-EST-{estado[:4]}-{uuid.uuid4().hex[:6]}"
        db_session.execute(
            text(
                "INSERT INTO ingesta_correos (entry_id, subject, sender_email, received_at, estado_revision) "
                f"VALUES ('{eid}', 'Asunto', 'a@b.com', now(), '{estado}')"
            )
        )
        db_session.flush()  # no exception

    def test_invalid_estado_rejected(self, db_session):
        with pytest.raises(IntegrityError):
            db_session.execute(
                text(
                    "INSERT INTO ingesta_correos (entry_id, subject, sender_email, received_at, estado_revision) "
                    "VALUES ('ENTRY-BAD-EST', 'Asunto', 'a@b.com', now(), 'INVALIDO')"
                )
            )
            db_session.flush()

    def test_aprobado_auto_accepted(self, db_session):
        """APROBADO_AUTO is the key new state — verify explicitly."""
        db_session.execute(
            text(
                "INSERT INTO ingesta_correos (entry_id, subject, sender_email, received_at, estado_revision) "
                "VALUES ('ENTRY-AUTO-001', 'Asunto auto', 'auto@inei.gob.pe', now(), 'APROBADO_AUTO')"
            )
        )
        db_session.flush()  # no exception


# ---------------------------------------------------------------------------
# (g) FK ingesta_documentos → ingesta_correos ON DELETE CASCADE
# ---------------------------------------------------------------------------

class TestIngestaDocumentosCascade:
    def test_cascade_delete(self, db_session):
        """Deleting the correo must cascade-delete its documentos."""
        eid = f"ENTRY-CASCADE-{uuid.uuid4().hex[:8]}"
        correo_id = _insert_correo(db_session, eid)

        db_session.execute(
            text(
                "INSERT INTO ingesta_documentos "
                "(ingesta_correo_id, nombre_original, nombre_almacenado, ruta_relativa, content_type, tamano_bytes) "
                f"VALUES ({correo_id}, 'doc.pdf', 'uuid.pdf', 'ingesta_staging/uuid.pdf', 'application/pdf', 12345)"
            )
        )
        db_session.flush()

        # Verify the doc is there
        cnt_before = db_session.execute(
            text(f"SELECT COUNT(*) FROM ingesta_documentos WHERE ingesta_correo_id={correo_id}")
        ).scalar()
        assert cnt_before == 1

        # Delete the correo — should cascade
        db_session.execute(
            text(f"DELETE FROM ingesta_correos WHERE id={correo_id}")
        )
        db_session.flush()

        cnt_after = db_session.execute(
            text(f"SELECT COUNT(*) FROM ingesta_documentos WHERE ingesta_correo_id={correo_id}")
        ).scalar()
        assert cnt_after == 0, "ingesta_documentos rows should cascade-delete with correo"


# ---------------------------------------------------------------------------
# (h) FK ingesta_correos.proceso_id ON DELETE SET NULL
# ---------------------------------------------------------------------------

class TestCorreoProcesoSetNull:
    def test_proceso_delete_sets_null(self, db_session):
        """Deleting the proceso must set correo.proceso_id to NULL (not cascade)."""
        proc_id = _make_proceso(db_session, "setnull")
        eid = f"ENTRY-SETNULL-{uuid.uuid4().hex[:8]}"

        db_session.execute(
            text(
                "INSERT INTO ingesta_correos "
                "(entry_id, subject, sender_email, received_at, proceso_id) "
                f"VALUES ('{eid}', 'Asunto', 'a@b.com', now(), {proc_id})"
            )
        )
        db_session.flush()

        correo_id = db_session.execute(
            text(f"SELECT id FROM ingesta_correos WHERE entry_id='{eid}'")
        ).scalar()

        # Delete the proceso
        db_session.execute(text(f"DELETE FROM procesos WHERE id={proc_id}"))
        db_session.flush()

        row = db_session.execute(
            text(f"SELECT proceso_id FROM ingesta_correos WHERE id={correo_id}")
        ).fetchone()
        assert row[0] is None, "proceso_id should be NULL after proceso deletion (SET NULL)"


# ---------------------------------------------------------------------------
# (i/j/k/l) Indexes exist
# ---------------------------------------------------------------------------

class TestIndexesExist:
    @pytest.mark.parametrize(
        "index_name",
        [
            "idx_ingesta_estado_revision",
            "idx_ingesta_proceso_id",
            "idx_ingesta_docs_correo",
            "idx_ingesta_docs_proceso",
            "idx_procesos_numero_oficio",
        ],
    )
    def test_index_exists(self, db_session, index_name):
        row = db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                f"WHERE indexname='{index_name}'"
            )
        ).fetchone()
        assert row is not None, f"Index {index_name} not found"
