"""Pure aggregation service: cross-process timing heatmap matrix.

Reuses tiempos_service.calcular_tiempos and etapas_service.agrupar_etapas
(both unchanged — called as-is) with batch-loaded EtapaRegistro rows.

Design decisions:
  - COLUMNAS_MATRIZ is the single source of truth for the column catalogue.
  - Import-time assert verifies each cod ∈ ETAPAS_CATALOGO; a missing cod
    raises AssertionError at startup, not silently at runtime.
  - construir_matriz is pure (no DB session, no IO) — trivially unit-testable.
  - TOTAL column is a virtual column appended to columnas; celdas list
    contains per-hito cells only (aligned to non-TOTAL columnas);
    total_dias is stored directly on FilaMatrizOut.
  - Row order: id_proceso ascending (stable for a heatmap).
"""
from __future__ import annotations

from collections import defaultdict

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso
from app.schemas.tiempos_matriz import ColumnaOut, FilaMatrizOut, MatrizTiemposOut
from app.services.etapas_catalogo import ETAPAS_CATALOGO
from app.services.etapas_service import agrupar_etapas
from app.services.tiempos_service import calcular_tiempos


# ---------------------------------------------------------------------------
# Column catalogue — single source of truth
# Tuple of (key, label, cod); TOTAL uses cod=None.
# ---------------------------------------------------------------------------

COLUMNAS_MATRIZ: tuple[tuple[str, str, str], ...] = (
    ("cmn",         "CMN",          "E01c"),
    ("tdr",         "TDR",          "E02"),
    ("vb",          "V°B°",         "E02b"),
    ("indagacion",  "Indagación",   "E03"),
    ("cuadro_comp", "Cuadro comp.", "E09"),
    ("os",          "O/S",          "E19"),
    ("conformidad", "Conformidad",  "E23"),
)

# Import-time assertion: each cod must be in ETAPAS_CATALOGO.
# A missing cod means the catalog drifted — fail loudly at startup.
assert all(
    cod in ETAPAS_CATALOGO for _, _, cod in COLUMNAS_MATRIZ
), (
    "COLUMNAS_MATRIZ contains a cod not found in ETAPAS_CATALOGO: "
    + str([cod for _, _, cod in COLUMNAS_MATRIZ if cod not in ETAPAS_CATALOGO])
)

# Pre-built ColumnaOut list (hito columns only, for internal use in aggregation)
_COLUMNAS_HITO: list[ColumnaOut] = [
    ColumnaOut(key=key, label=label, cod=cod)
    for key, label, cod in COLUMNAS_MATRIZ
]

# Full columnas list sent to the frontend (hito columns + TOTAL)
_COLUMNAS_FULL: list[ColumnaOut] = _COLUMNAS_HITO + [
    ColumnaOut(key="total", label="TOTAL", cod=None)
]

# Ordered list of hito cods (for fast cell projection)
_HITO_CODS: list[str] = [cod for _, _, cod in COLUMNAS_MATRIZ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def construir_matriz(
    procesos: list[Proceso],
    etapas_rows: list[EtapaRegistro],
) -> MatrizTiemposOut:
    """Build the cross-process timing heatmap from pre-loaded data.

    Args:
        procesos: Filtered Proceso objects (already ordered by id_proceso asc).
        etapas_rows: ALL EtapaRegistro rows for the given proceso_ids,
                     loaded in a single IN(...) query by the router.

    Returns:
        MatrizTiemposOut (pure — no DB session, no IO).
    """
    # Group etapa rows by proceso_id
    by_proceso: dict[int, list[EtapaRegistro]] = defaultdict(list)
    for row in etapas_rows:
        if row.proceso_id is not None:
            by_proceso[row.proceso_id].append(row)

    filas: list[FilaMatrizOut] = []
    # Per-column accumulator: list of non-null cell values
    col_values: list[list[int]] = [[] for _ in _HITO_CODS]
    total_values: list[int] = []

    for proceso in procesos:
        rows_of_proceso = by_proceso.get(proceso.id, [])
        agrupadas = agrupar_etapas(rows_of_proceso)
        t = calcular_tiempos(agrupadas)

        # Project cells by cod
        cell_map = {iv.cod: iv.dias for iv in t.intervalos}
        celdas: list[int | None] = []
        for i, cod in enumerate(_HITO_CODS):
            val = cell_map.get(cod)
            celdas.append(val)
            if val is not None:
                col_values[i].append(val)

        filas.append(FilaMatrizOut(
            proceso_id=proceso.id,
            id_proceso=proceso.id_proceso,
            requerimiento=proceso.requerimiento,
            pim=float(proceso.pim) if proceso.pim is not None else None,
            estado=proceso.estado,
            tipo=proceso.tipo,
            celdas=celdas,
            total_dias=t.total_dias,
        ))
        total_values.append(t.total_dias)

    # Per-column averages (non-null cells only; null if no proceso has data)
    promedios: list[float | None] = [
        (sum(vals) / len(vals)) if vals else None
        for vals in col_values
    ]

    # Average of total_dias (append as the TOTAL column average)
    promedio_total: float | None = (
        sum(total_values) / len(total_values) if total_values else None
    )

    # Append promedio_total to promedios so it aligns with _COLUMNAS_FULL
    promedios_full = promedios + [promedio_total]

    return MatrizTiemposOut(
        columnas=_COLUMNAS_FULL,
        filas=filas,
        promedios=promedios_full,
        promedio_total=promedio_total,
    )
