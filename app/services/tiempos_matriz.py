"""Pure aggregation service for the cross-process timing heatmap."""
from __future__ import annotations

from collections import defaultdict

from app.models.etapa import EtapaRegistro
from app.models.proceso import Proceso
from app.schemas.tiempos_matriz import ColumnaOut, FilaMatrizOut, MatrizTiemposOut
from app.services.etapas_catalogo import ETAPAS_CATALOGO
from app.services.etapas_service import agrupar_etapas
from app.services.tiempos_service import calcular_tiempos


COLUMNAS_MATRIZ: tuple[tuple[str, str, str], ...] = (
    ("cmn", "CMN", "E01c"),
    ("tdr", "TDR", "E02"),
    ("vb", "V°B°", "E02b"),
    ("indagacion", "Indagación", "E03"),
    ("cuadro_comp", "Cuadro comp.", "E09"),
    ("os", "O/S", "E19"),
    ("conformidad", "Conformidad", "E23"),
)

assert all(cod in ETAPAS_CATALOGO for _, _, cod in COLUMNAS_MATRIZ), (
    "COLUMNAS_MATRIZ contains a cod not found in ETAPAS_CATALOGO: "
    + str([cod for _, _, cod in COLUMNAS_MATRIZ if cod not in ETAPAS_CATALOGO])
)

_COLUMNAS_HITO: list[ColumnaOut] = [
    ColumnaOut(key=key, label=label, cod=cod)
    for key, label, cod in COLUMNAS_MATRIZ
]

_COLUMNAS_FULL: list[ColumnaOut] = _COLUMNAS_HITO + [
    ColumnaOut(key="total", label="TOTAL", cod=None)
]

_HITO_CODS: list[str] = [cod for _, _, cod in COLUMNAS_MATRIZ]


def construir_matriz(
    procesos: list[Proceso],
    etapas_rows: list[EtapaRegistro],
) -> MatrizTiemposOut:
    """Build the heatmap response from preloaded process and stage rows."""
    by_proceso: dict[int, list[EtapaRegistro]] = defaultdict(list)
    for row in etapas_rows:
        if row.proceso_id is not None:
            by_proceso[row.proceso_id].append(row)

    filas: list[FilaMatrizOut] = []
    col_values: list[list[int]] = [[] for _ in _HITO_CODS]
    total_values: list[int] = []

    for proceso in procesos:
        agrupadas = agrupar_etapas(by_proceso.get(proceso.id, []))
        tiempos = calcular_tiempos(agrupadas)
        cell_map = {intervalo.cod: intervalo.dias for intervalo in tiempos.intervalos}

        celdas: list[int | None] = []
        for i, cod in enumerate(_HITO_CODS):
            value = cell_map.get(cod)
            celdas.append(value)
            if value is not None:
                col_values[i].append(value)

        filas.append(
            FilaMatrizOut(
                proceso_id=proceso.id,
                id_proceso=proceso.id_proceso,
                requerimiento=proceso.requerimiento,
                pim=float(proceso.pim) if proceso.pim is not None else None,
                estado=proceso.estado,
                tipo=proceso.tipo,
                celdas=celdas,
                total_dias=tiempos.total_dias,
            )
        )
        total_values.append(tiempos.total_dias)

    promedios: list[float | None] = [
        (sum(values) / len(values)) if values else None
        for values in col_values
    ]
    promedio_total = sum(total_values) / len(total_values) if total_values else None

    return MatrizTiemposOut(
        columnas=_COLUMNAS_FULL,
        filas=filas,
        promedios=promedios + [promedio_total],
        promedio_total=promedio_total,
    )
