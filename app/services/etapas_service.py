"""Service layer for etapas — C3a mechanics + C3b rules wired.

Business rule enforcement via validaciones.py (C3b).
Audit (historial_cambios) is written on PUT and on state transitions.

Key design decisions:
  D2 — progreso is derived from rows, never persisted.
  D4 — GET response is grouped: rondas[] for loop stages, filas[] for others.
  D3 — R2 (E10 SIN_PRESUPUESTO) → proceso CANCELADO; R5 (E25 COMPLETADO) → CULMINADO.

Functions > 50 lines are split into private helpers per coding conventions.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.etapa import EtapaRegistro
from app.models.historial import HistorialCambio
from app.models.montos import MontosProceso
from app.models.proceso import Proceso
from app.schemas.etapa import (
    BucleCreate,
    EtapaAgrupadaOut,
    EtapaCreate,
    EtapaUpdate,
    FilaAreaOut,
    ProgresoOut,
    RondaBucleOut,
)
from app.services.etapas_catalogo import (
    ETAPAS_CATALOGO,
    ORDEN_ETAPAS,
    PROGRESO_DENOMINATOR,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BUCLE_CODS: frozenset[str] = frozenset(
    cod for cod, spec in ETAPAS_CATALOGO.items() if spec.es_bucle
)


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def registrar_etapa(
    db: Session,
    proceso_id: int,
    payload: EtapaCreate,
    current_user_username: str,
) -> EtapaRegistro:
    """Insert a new etapas_registro row with C3b rule validation.

    For por_area stages (E01, E11, E24): if a row already exists for the same
    (proceso_id, codigo_etapa, area_usuaria), UPDATE it instead of INSERTing a
    duplicate.  Simple and bucle stages always INSERT (unchanged behaviour).

    Calls validaciones before persisting; runs montos sync and state
    transitions after persisting (within the same transaction).
    """
    from app.services.validaciones import (
        validar_proceso_activo,
        validar_prerequisito_generico,
        validar_r2_e10,
        validar_r3_e12,
        validar_r5_e25,
        validar_r7_e09,
    )

    # --- Gate: proceso must not be CANCELADO (always applies, even for NO_APLICA) ---
    validar_proceso_activo(db, proceso_id)

    cod = payload.codigo_etapa

    # --- NO_APLICA fast-path: skip prereqs and all stage-specific rules ---
    # A stage marked NO_APLICA is intentionally skipped (e.g. direct service order
    # bypasses budget certification block E09-E18).  It still satisfies prerequisites
    # for subsequent stages (handled in validar_prerequisito_generico).
    if payload.estado_etapa != "NO_APLICA":
        # --- Generic prerequisite check (covers E02←E01, E09←E08, E12←E11, E25←E24) ---
        validar_prerequisito_generico(db, proceso_id, cod)

        # --- Stage-specific rules ---
        if cod == "E10":
            validar_r2_e10(db, proceso_id, payload)
        if cod == "E12":
            validar_r3_e12(db, proceso_id)
        if cod == "E09":
            validar_r7_e09(db, proceso_id)
        if cod == "E25":
            validar_r5_e25(db, proceso_id)

    spec = ETAPAS_CATALOGO.get(cod)
    es_bucle = spec.es_bucle if spec else False

    # --- Por-area idempotent upsert (E01, E11, E24) ---
    # When the stage is per-area and area_usuaria is provided, check for an
    # existing row.  If found, update it in place to avoid duplicate rows.
    # Bucle stages are never por_area, so this branch never interferes with them.
    if spec is not None and spec.por_area and payload.area_usuaria:
        existing = db.execute(
            select(EtapaRegistro).where(
                EtapaRegistro.proceso_id == proceso_id,
                EtapaRegistro.codigo_etapa == cod,
                EtapaRegistro.area_usuaria == payload.area_usuaria,
            )
        ).scalars().first()

        if existing is not None:
            # UPDATE path: apply all provided fields and run post-persist hooks.
            _upsert_por_area_fields(existing, payload, current_user_username)
            db.flush()
            sync_montos(db, proceso_id, cod, existing)
            _aplicar_transicion_estado_proceso(
                db, proceso_id, cod, payload, existing, current_user_username
            )
            return existing

    # --- Normal INSERT path (simple, bucle, or first por_area row for an area) ---
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=payload.nombre_etapa,
        area_responsable=spec.area_responsable if spec else None,
        fecha_inicio=payload.fecha_inicio,
        fecha_fin=payload.fecha_fin,
        estado_etapa=payload.estado_etapa,
        responsable=payload.responsable,
        oficio_correo=payload.oficio_correo,
        observaciones=payload.observaciones,
        area_usuaria=payload.area_usuaria,
        cmn_adjunto=payload.cmn_adjunto,
        monto_cert=payload.monto_cert,
        resultado_eval=payload.resultado_eval,
        motivo_bucle=payload.motivo_bucle,
        fecha_envio_otpp=payload.fecha_envio_otpp,
        fecha_resp_otpp=payload.fecha_resp_otpp,
        nro_ocs=payload.nro_ocs,
        monto_ocs=payload.monto_ocs,
        plazo_entrega=payload.plazo_entrega,
        es_bucle=es_bucle,
        nro_ronda=1,
        registrado_por=current_user_username,
        # flujo-real-otin-v2
        fecha_limite_respuesta=getattr(payload, "fecha_limite_respuesta", None),
        cmn_siga_confirmado=getattr(payload, "cmn_siga_confirmado", None),
    )
    db.add(row)
    db.flush()

    # --- Post-persist: montos sync + state transitions ---
    sync_montos(db, proceso_id, cod, row)
    _aplicar_transicion_estado_proceso(
        db, proceso_id, cod, payload, row, current_user_username
    )

    return row


def _upsert_por_area_fields(
    row: EtapaRegistro,
    payload: EtapaCreate,
    current_user_username: str,
) -> None:
    """Apply mutable fields from payload onto an existing por_area row in place.

    Only overwrites a field when the payload provides a non-None value, so
    partial updates (e.g. only monto_cert changed) work correctly.
    """
    _updatable: tuple[str, ...] = (
        "nombre_etapa",
        "fecha_inicio",
        "fecha_fin",
        "estado_etapa",
        "responsable",
        "oficio_correo",
        "observaciones",
        "cmn_adjunto",
        "monto_cert",
        "resultado_eval",
        "fecha_envio_otpp",
        "fecha_resp_otpp",
        "nro_ocs",
        "monto_ocs",
        "plazo_entrega",
        # flujo-real-otin-v2
        "fecha_limite_respuesta",
        "cmn_siga_confirmado",
    )
    for campo in _updatable:
        nuevo = getattr(payload, campo, None)
        if nuevo is not None:
            setattr(row, campo, nuevo)
    row.actualizado_por = current_user_username
    row.actualizado_en = datetime.now(timezone.utc).replace(tzinfo=None)


def actualizar_etapa(
    db: Session,
    etapa: EtapaRegistro,
    payload: EtapaUpdate,
    current_user_username: str,
) -> EtapaRegistro:
    """Update an existing etapas_registro row; audit each changed field."""
    _mutable_fields = [
        "nombre_etapa", "fecha_inicio", "fecha_fin", "estado_etapa",
        "responsable", "oficio_correo", "observaciones", "area_usuaria",
        "cmn_adjunto", "monto_cert", "resultado_eval", "motivo_bucle",
        "fecha_envio_otpp", "fecha_resp_otpp", "nro_ocs", "monto_ocs",
        "plazo_entrega",
    ]
    for campo in _mutable_fields:
        nuevo = getattr(payload, campo, None)
        if nuevo is None:
            continue
        antes = getattr(etapa, campo)
        if str(antes) == str(nuevo):
            continue
        _registrar_auditoria(
            db,
            proceso_id=etapa.proceso_id,
            etapa_id=etapa.id,
            campo=campo,
            antes=str(antes) if antes is not None else None,
            nuevo=str(nuevo),
            usuario=current_user_username,
        )
        setattr(etapa, campo, nuevo)

    etapa.actualizado_por = current_user_username
    etapa.actualizado_en = datetime.now(timezone.utc).replace(tzinfo=None)
    db.flush()

    # Sync montos if this update completes a trigger stage
    if etapa.proceso_id is not None:
        sync_montos(db, etapa.proceso_id, etapa.codigo_etapa, etapa)

    return etapa


def agregar_ronda_bucle(
    db: Session,
    proceso_id: int,
    cod: str,
    payload: BucleCreate,
    current_user_username: str,
) -> EtapaRegistro:
    """Add a new round for a loop-type stage with C3b rule validation.

    Validates R6 (E05/E06 require E04 COMPLETADO) and generic prereqs before
    inserting. Computes nro_ronda = MAX(nro_ronda WHERE proceso_id, cod) + 1.
    """
    from app.services.validaciones import (
        validar_proceso_activo,
        validar_prerequisito_generico,
        validar_r6_bucle_tdr,
    )

    validar_proceso_activo(db, proceso_id)

    if cod in ("E05", "E06"):
        validar_r6_bucle_tdr(db, proceso_id)

    # Generic prereq covers E08a/E08b (prerequisitos defined in catalogo)
    validar_prerequisito_generico(db, proceso_id, cod)

    max_ronda = db.execute(
        select(func.max(EtapaRegistro.nro_ronda)).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == cod,
        )
    ).scalar_one_or_none() or 0

    spec = ETAPAS_CATALOGO.get(cod)
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=spec.nombre if spec else cod,
        area_responsable=spec.area_responsable if spec else None,
        es_bucle=True,
        nro_ronda=max_ronda + 1,
        motivo_bucle=payload.motivo_bucle,
        estado_etapa="PENDIENTE",
        registrado_por=current_user_username,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Audit helper (Spec §F — only called from actualizar_etapa)
# ---------------------------------------------------------------------------

def _registrar_auditoria(
    db: Session,
    proceso_id: int | None,
    etapa_id: int,
    campo: str,
    antes: str | None,
    nuevo: str,
    usuario: str,
) -> None:
    """Insert one historial_cambios row for a changed field."""
    db.add(
        HistorialCambio(
            proceso_id=proceso_id,
            etapa_id=etapa_id,
            campo_modificado=campo,
            valor_anterior=antes,
            valor_nuevo=nuevo,
            modificado_por=usuario,
        )
    )


# ---------------------------------------------------------------------------
# Montos sync (WU-B2 — replaces C3a TODO stub)
# ---------------------------------------------------------------------------

def sync_montos(
    db: Session,
    proceso_id: int,
    cod: str,
    etapa_row: EtapaRegistro,
) -> None:
    """Upsert montos_proceso when a trigger stage reaches COMPLETADO.

    Trigger stages: E09 → valor_em; E12 → monto_cert_total (sum E11);
    E19 → nro_ocs/monto_ocs/plazo_entrega; E22 → fecha_inicio_srv.

    vencimiento_ocs is DERIVED on GET (fecha_inicio + plazo_entrega) — never stored.
    """
    if etapa_row.estado_etapa != "COMPLETADO":
        return
    if cod not in ("E09", "E12", "E19", "E22"):
        return

    # Build the set of columns to update depending on the trigger stage
    if cod == "E09":
        update_values: dict = {"valor_em": etapa_row.monto_cert}
    elif cod == "E12":
        total = db.execute(
            select(func.sum(EtapaRegistro.monto_cert)).where(
                EtapaRegistro.proceso_id == proceso_id,
                EtapaRegistro.codigo_etapa == "E11",
            )
        ).scalar_one_or_none() or Decimal("0.00")
        update_values = {"monto_cert_total": total}
    elif cod == "E19":
        update_values = {
            "nro_ocs": etapa_row.nro_ocs,
            "monto_ocs": etapa_row.monto_ocs,
            "plazo_entrega": etapa_row.plazo_entrega,
        }
    elif cod == "E22":
        update_values = {"fecha_inicio_srv": etapa_row.fecha_inicio}
    else:
        return  # unreachable but guard

    stmt = pg_insert(MontosProceso).values(proceso_id=proceso_id, **update_values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["proceso_id"],
        set_=update_values,
    )
    db.execute(stmt)
    db.flush()


# ---------------------------------------------------------------------------
# State transitions (Design D3 — R2 CANCELADO, R5 CULMINADO)
# ---------------------------------------------------------------------------

def _aplicar_transicion_estado_proceso(
    db: Session,
    proceso_id: int,
    cod: str,
    payload,
    etapa_row: EtapaRegistro,
    current_user_username: str,
) -> None:
    """Apply proceso state transitions driven by business rules R2 and R5.

    R2: E10 + resultado_eval='SIN_PRESUPUESTO' → proceso CANCELADO + motivo_cancel.
    R5: E25 + estado_etapa='COMPLETADO' → proceso CULMINADO.
        fecha_fin_total: Design decision — E25.fecha_fin is the canonical end date;
        no new column on procesos. GET /procesos/{id} derives it from E25 row.
    """
    if cod == "E10" and getattr(payload, "resultado_eval", None) == "SIN_PRESUPUESTO":
        proceso = db.get(Proceso, proceso_id)
        if proceso:
            estado_antes = proceso.estado
            proceso.estado = "CANCELADO"
            proceso.motivo_cancel = getattr(payload, "motivo_cancel", None)
            _registrar_auditoria(
                db,
                proceso_id=proceso_id,
                etapa_id=etapa_row.id,
                campo="proceso.estado",
                antes=estado_antes,
                nuevo="CANCELADO",
                usuario=current_user_username,
            )

    elif cod == "E25" and etapa_row.estado_etapa == "COMPLETADO":
        proceso = db.get(Proceso, proceso_id)
        if proceso:
            estado_antes = proceso.estado
            proceso.estado = "CULMINADO"
            _registrar_auditoria(
                db,
                proceso_id=proceso_id,
                etapa_id=etapa_row.id,
                campo="proceso.estado",
                antes=estado_antes,
                nuevo="CULMINADO",
                usuario=current_user_username,
            )


# ---------------------------------------------------------------------------
# Reiniciar TDR (Design D3 — endpoint POST /procesos/{id}/reiniciar-tdr)
# ---------------------------------------------------------------------------

#: Códigos a marcar OMITIDO en el reinicio (E02..E09 inclusive con bucles)
#: flujo-real-otin-v2: E02b added (between E02 and E03 in CADENA)
_CODIGOS_REINICIO: tuple[str, ...] = (
    "E02", "E02b", "E03", "E04", "E05", "E06", "E06c", "E07", "E08", "E08a", "E08b", "E09"
)


def reiniciar_tdr(
    db: Session,
    proceso_id: int,
    current_user_username: str,
) -> EtapaRegistro:
    """Reinicia el flujo TDR desde E02 en un proceso CANCELADO por E10 SIN_PRESUPUESTO.

    Operación transaccional (el router hace db.commit):
    1. Valida precondiciones (proceso CANCELADO + E10 SIN_PRESUPUESTO).
    2. Marca OMITIDO todas las filas E02-E09 activas (preserva auditoría).
    3. Inserta nueva fila E02 PENDIENTE con nro_ronda incrementado.
    4. Restaura proceso.estado='EN PROCESO', limpia motivo_cancel activo.
    5. Registra auditoría del cambio de estado.

    E01 (CMN) NO se toca — las áreas ya entregaron requerimiento (Design D3).
    El progreso recalcula solo en GET (OMITIDO no cuenta como COMPLETADO).
    """
    from app.services.validaciones import validar_reinicio_tdr

    validar_reinicio_tdr(db, proceso_id)

    # Compute nro_ronda for the new E02
    max_ronda = db.execute(
        select(func.max(EtapaRegistro.nro_ronda)).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == "E02",
        )
    ).scalar_one_or_none() or 0
    nro_ronda_reinicio = max_ronda + 1

    # Mark E02-E09 rows as OMITIDO (idempotent: skip already-OMITIDO rows)
    filas_a_omitir = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa.in_(_CODIGOS_REINICIO),
            EtapaRegistro.estado_etapa != "OMITIDO",
        )
    ).scalars().all()
    for fila in filas_a_omitir:
        fila.estado_etapa = "OMITIDO"
    db.flush()

    # Insert new E02 PENDIENTE
    spec = ETAPAS_CATALOGO.get("E02")
    nueva_e02 = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa="E02",
        nombre_etapa=spec.nombre if spec else "Elaboración TDR consolidado",
        area_responsable=spec.area_responsable if spec else "OTIN",
        es_bucle=False,
        nro_ronda=nro_ronda_reinicio,
        estado_etapa="PENDIENTE",
        observaciones=(
            f"Reinicio TDR tras cancelación E10 (ronda {nro_ronda_reinicio})"
        ),
        registrado_por=current_user_username,
    )
    db.add(nueva_e02)
    db.flush()

    # Restore proceso state
    proceso = db.get(Proceso, proceso_id)
    if proceso:
        proceso.estado = "EN PROCESO"
        proceso.motivo_cancel = None

    # Audit the state change
    _registrar_auditoria(
        db,
        proceso_id=proceso_id,
        etapa_id=nueva_e02.id,
        campo="proceso.estado",
        antes="CANCELADO",
        nuevo="EN PROCESO",
        usuario=current_user_username,
    )

    db.flush()
    return nueva_e02


# ---------------------------------------------------------------------------
# Progreso calculation (pure function — Design D2)
# ---------------------------------------------------------------------------

def calcular_progreso(etapas_rows: list[EtapaRegistro]) -> ProgresoOut:
    """Derive etapa_actual and progress % from etapas_registro rows.

    Algorithm (Design D2 + NO_APLICA extension):
    - Iterate ORDEN_ETAPAS.
    - Per cod, gather all rows for that cod.
    - Estado consolidado = COMPLETADO if:
        - por_area=True → ALL rows are COMPLETADO (OMITIDO treated as non-completado)
        - es_bucle=True → last ronda (highest nro_ronda) is COMPLETADO
        - simple  → single row is COMPLETADO
    - Estado consolidado = NO_APLICA if all rows for that cod are NO_APLICA
      (for simple and por_area stages; bucle: last ronda is NO_APLICA).
    - etapa_actual = first cod whose consolidated estado is not COMPLETADO
      and not NO_APLICA (skipped stages do not block the current pointer).
    - progreso % = (completadas / denominator) * 100
    - denominator = PROGRESO_DENOMINATOR (26) minus count of non-bucle stages
      whose consolidated estado is NO_APLICA (minimum 1 to avoid division by zero).
    - PROGRESO_DENOMINATOR = 26 (baseline; bucle cods excluded per Design D2).
    - CULMINADO override: if proceso is CULMINADO the caller sets porcentaje=100
      directly (handled in GET route, not here); here we just compute from rows.
    """
    # Group rows by codigo_etapa
    by_cod: dict[str, list[EtapaRegistro]] = {}
    for row in etapas_rows:
        by_cod.setdefault(row.codigo_etapa, []).append(row)

    completadas = 0
    no_aplica_count = 0  # non-bucle stages marked NO_APLICA
    etapa_actual: str | None = None

    for cod in ORDEN_ETAPAS:
        spec = ETAPAS_CATALOGO[cod]
        rows_for_cod = by_cod.get(cod, [])

        estado = _estado_consolidado(spec, rows_for_cod)

        if estado == "COMPLETADO":
            # Only count non-bucle cods in progress numerator
            if not spec.es_bucle:
                completadas += 1
        elif estado == "NO_APLICA":
            # Non-bucle NO_APLICA stages are excluded from denominator
            if not spec.es_bucle:
                no_aplica_count += 1
            # NO_APLICA does NOT block etapa_actual (skip it like COMPLETADO)
        else:
            # Bug #3 fix: bucle stages (E05/E06/E06b/E08a/E08b) are optional loops
            # and must NEVER be chosen as etapa_actual, even when empty/PENDIENTE.
            if etapa_actual is None and not spec.es_bucle:
                etapa_actual = cod

    # Default to first stage when no rows at all
    if etapa_actual is None and completadas == 0 and no_aplica_count == 0:
        etapa_actual = ORDEN_ETAPAS[0] if ORDEN_ETAPAS else None

    # Dynamic denominator: subtract NO_APLICA stages from the base denominator
    denominator = max(1, PROGRESO_DENOMINATOR - no_aplica_count)

    porcentaje = round((completadas / denominator) * 100, 1)

    return ProgresoOut(
        etapa_actual=etapa_actual,
        porcentaje=porcentaje,
        completadas=completadas,
        total=denominator,
    )


def _estado_consolidado(spec, rows: list[EtapaRegistro]) -> str:
    """Compute the consolidated estado for one stage code.

    OMITIDO rows are treated as non-completado (Design D3 — reinicio TDR
    marks them OMITIDO to preserve audit, but they don't count as done).
    NO_APLICA: stage was intentionally skipped. Treated as satisfied for
    prerequisites and excluded from the progress denominator.
    """
    if not rows:
        return "PENDIENTE"

    if spec.es_bucle:
        # Last ronda (highest nro_ronda) determines the estado
        last = max(rows, key=lambda r: r.nro_ronda)
        return last.estado_etapa

    if spec.por_area:
        # ALL rows must be COMPLETADO or NO_APLICA
        _done = {"COMPLETADO", "NO_APLICA"}
        if all(r.estado_etapa in _done for r in rows):
            # Distinguish: all NO_APLICA → return NO_APLICA; mixed/all COMPLETADO → COMPLETADO
            if all(r.estado_etapa == "NO_APLICA" for r in rows):
                return "NO_APLICA"
            return "COMPLETADO"
        if any(r.estado_etapa in ("EN_CURSO", "COMPLETADO") for r in rows):
            return "EN_CURSO"
        return "PENDIENTE"

    # Simple stage: single row (take first, there should only be one)
    row = rows[0]
    return row.estado_etapa


# ---------------------------------------------------------------------------
# Grouping for GET response (Design D4)
# ---------------------------------------------------------------------------

def agrupar_etapas(
    etapas_rows: list[EtapaRegistro],
    areas_usuarias: list[str] | None = None,
) -> list[EtapaAgrupadaOut]:
    """Group etapas_registro rows into the canonical GET response format.

    One EtapaAgrupadaOut per cod in ORDEN_ETAPAS (all 27 entries, even with
    no rows — those appear as PENDIENTE with empty filas/rondas).
    """
    by_cod: dict[str, list[EtapaRegistro]] = {}
    for row in etapas_rows:
        by_cod.setdefault(row.codigo_etapa, []).append(row)

    today = date.today()
    result: list[EtapaAgrupadaOut] = []

    for cod in ORDEN_ETAPAS:
        spec = ETAPAS_CATALOGO[cod]
        rows = by_cod.get(cod, [])
        estado = _estado_consolidado(spec, rows)

        entry = EtapaAgrupadaOut(
            cod=cod,
            nombre=spec.nombre,
            area_responsable=spec.area_responsable,
            es_bucle=spec.es_bucle,
            por_area=spec.por_area,
            estado=estado,
        )

        if spec.es_bucle:
            entry.rondas = _build_rondas(rows)
        else:
            entry.filas = _build_filas(rows, cod)

        if cod == "E16":
            entry.alerta_otpp = _calcular_alerta_e16(rows, today)

        if cod == "E11" and rows:
            entry.monto_total = _sum_monto_cert(rows)

        result.append(entry)

    return result


def _build_rondas(rows: list[EtapaRegistro]) -> list[RondaBucleOut]:
    """Build rondas list sorted by nro_ronda ASC."""
    sorted_rows = sorted(rows, key=lambda r: r.nro_ronda)
    return [
        RondaBucleOut(
            id=r.id,
            nro_ronda=r.nro_ronda,
            motivo_bucle=r.motivo_bucle,
            estado_etapa=r.estado_etapa,
            fecha_inicio=r.fecha_inicio,
            fecha_fin=r.fecha_fin,
            dias=r.dias,
        )
        for r in sorted_rows
    ]


def _build_filas(rows: list[EtapaRegistro], cod: str) -> list[FilaAreaOut]:
    """Build filas list sorted by area_usuaria ASC (then id for stability)."""
    sorted_rows = sorted(
        rows,
        key=lambda r: (r.area_usuaria or "", r.id),
    )
    filas = []
    for r in sorted_rows:
        vencimiento_ocs = _calcular_vencimiento_ocs(r) if cod == "E19" else None
        filas.append(
            FilaAreaOut(
                id=r.id,
                area_usuaria=r.area_usuaria,
                estado_etapa=r.estado_etapa,
                fecha_inicio=r.fecha_inicio,
                fecha_fin=r.fecha_fin,
                dias=r.dias,
                cmn_adjunto=r.cmn_adjunto,
                monto_cert=r.monto_cert,
                resultado_eval=r.resultado_eval,
                nro_ocs=r.nro_ocs,
                monto_ocs=r.monto_ocs,
                plazo_entrega=r.plazo_entrega,
                fecha_envio_otpp=r.fecha_envio_otpp,
                fecha_resp_otpp=r.fecha_resp_otpp,
                responsable=r.responsable,
                oficio_correo=r.oficio_correo,
                observaciones=r.observaciones,
                registrado_por=r.registrado_por,
                vencimiento_ocs=vencimiento_ocs,
            )
        )
    return filas


def _calcular_vencimiento_ocs(row: EtapaRegistro) -> date | None:
    """Compute vencimiento_ocs = fecha_inicio + plazo_entrega days (derived, never stored)."""
    from datetime import timedelta
    if row.fecha_inicio and row.plazo_entrega is not None:
        return row.fecha_inicio + timedelta(days=row.plazo_entrega)
    return None


def _calcular_alerta_e16(rows: list[EtapaRegistro], today: date) -> bool:
    """Return True if any E16 row exceeds the 20-day response threshold.

    Triggered when:
    - fecha_resp_otpp - fecha_envio_otpp > 20 days, OR
    - today - fecha_envio_otpp > 20 days AND fecha_resp_otpp is None
    """
    for r in rows:
        if r.fecha_envio_otpp is None:
            continue
        if r.fecha_resp_otpp is not None:
            if (r.fecha_resp_otpp - r.fecha_envio_otpp).days > 20:
                return True
        else:
            if (today - r.fecha_envio_otpp).days > 20:
                return True
    return False


def _sum_monto_cert(rows: list[EtapaRegistro]) -> Decimal | None:
    """Sum monto_cert across all E11 rows (None if no values)."""
    total: Decimal = Decimal("0.00")
    has_value = False
    for r in rows:
        if r.monto_cert is not None:
            total += r.monto_cert
            has_value = True
    return total if has_value else None
