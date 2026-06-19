"""Catálogo de las 31 etapas del flujo de adquisición TIC — flujo-real-otin-v2.

Config en código (dict Python frozen dataclasses). Espeja 1:1 a
ETAPAS_CONFIG de frontend/src/lib/constants.ts — ambos derivan de CONTEXT.md §8.

Sin dependencias de BD ni I/O: importable en cualquier contexto (tests, scripts).

Decisión de diseño (D1): NO tabla DB. Estático, versionado, testeable; las 31 filas
cambian solo con deploy de código → no overhead de seed/migración/sincronía.

C3c: Cadena principal (26 nodos) con prerequisito secuencial derivado via
dataclasses.replace. Bucles (E05/E06/E06b/E06c/E08a/E08b) NO están en la cadena.
acepta_adjuntos=True en etapas clave que producen/reciben documentos formales.

flujo-real-otin-v2 changes vs v1:
  - E01 REMOVED; replaced by E01a + E01b + E01c
  - E01a: nuevo, raíz de la cadena, area_responsable=AREAS, no por_area
  - E01b: nuevo, dep E01a, area_responsable=OTIN, acepta fecha_limite_respuesta
  - E01c: renombrado de E01, por_area=True, acepta cmn_siga_confirmado
  - E02b: nuevo, dep E02, V°B° secuencial áreas del TDR (tracked via firma_secuencial)
  - E06c: nuevo bucle, re-V°B° secuencial post-corrección
  - E03 ahora dep E02b (no E02 directamente)
  CADENA: 26 nodos (32 total - 6 bucles: E05, E06, E06b, E06c, E08a, E08b)
  PROGRESO_DENOMINATOR: 26 (updated from 25; E02b added to main chain)
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True)
class EtapaSpec:
    cod: str
    orden: int
    area_responsable: str
    nombre: str
    campos_extra: tuple[str, ...] = ()
    es_bucle: bool = False
    por_area: bool = False
    es_fin: bool = False
    prerequisitos: tuple[str, ...] = ()
    alerta_dias: int | None = None
    acepta_adjuntos: bool = False


# ---------------------------------------------------------------------------
# Catálogo canónico — 31 entradas
# Prerequisitos clave (Design D1):
#   E01a → E01b → E01c → E02 → E02b → E03 → E04 → ... → E25
#   E05/E06 ← E04; E08a/E08b ← E08
#   E12 ← E11; E25 ← E24
# ---------------------------------------------------------------------------

ETAPAS_CATALOGO: dict[str, EtapaSpec] = {
    # ------------------------------------------------------------------
    # FASE 1 — Requerimiento y TDR
    # ------------------------------------------------------------------
    "E01a": EtapaSpec(
        cod="E01a", orden=1, area_responsable="AREAS",
        nombre="Solicitud inicial área iniciadora (Área → OTIN)",
        acepta_adjuntos=True,
    ),
    "E01b": EtapaSpec(
        cod="E01b", orden=2, area_responsable="OTIN",
        nombre="Oficio circular OTIN → áreas usuarias",
        campos_extra=("fecha_limite_respuesta",),
        acepta_adjuntos=True,
        prerequisitos=("E01a",),
    ),
    "E01c": EtapaSpec(
        cod="E01c", orden=3, area_responsable="AREAS",
        nombre="Respuesta área con requerimiento + CMN/SIGA (Áreas → OTIN)",
        campos_extra=("cmn_siga_confirmado",),
        por_area=True,
        acepta_adjuntos=True,
        prerequisitos=("E01b",),
    ),
    "E02": EtapaSpec(
        cod="E02", orden=4, area_responsable="OTIN",
        nombre="Elaboración TDR consolidado (OTIN)",
        prerequisitos=("E01c",),
        acepta_adjuntos=True,
    ),
    "E02b": EtapaSpec(
        cod="E02b", orden=5, area_responsable="AREAS",
        nombre="V°B° secuencial áreas del TDR",
        prerequisitos=("E02",),
        acepta_adjuntos=False,
    ),
    "E03": EtapaSpec(
        cod="E03", orden=6, area_responsable="OTIN",
        nombre="Envío indagación de mercado (OTIN → OTA)",
        acepta_adjuntos=True,
        prerequisitos=("E02b",),
    ),
    "E04": EtapaSpec(
        cod="E04", orden=7, area_responsable="OTA",
        nombre="OTA deriva expediente a OEAS (OTA → OEAS)",
    ),
    # ------------------------------------------------------------------
    # FASE 2 — Indagación y Evaluación (bucles + evaluación técnica)
    # ------------------------------------------------------------------
    "E05": EtapaSpec(
        cod="E05", orden=8, area_responsable="BUCLE",
        nombre="Observaciones al TDR [BUCLE] (OEAS → OTIN)",
        campos_extra=("motivo_bucle",),
        es_bucle=True,
        prerequisitos=("E04",),
    ),
    "E06": EtapaSpec(
        cod="E06", orden=9, area_responsable="BUCLE",
        nombre="Corrección TDR [BUCLE] (OTIN → OEAS)",
        campos_extra=("motivo_bucle",),
        es_bucle=True,
        prerequisitos=("E04",),
        acepta_adjuntos=True,
    ),
    "E06b": EtapaSpec(
        cod="E06b", orden=10, area_responsable="BUCLE",
        nombre="Solicitud V°B° DTDIS [BUCLE] (OTIN → DTDIS)",
        campos_extra=("motivo_bucle",),
        es_bucle=True,
        acepta_adjuntos=True,
    ),
    "E06c": EtapaSpec(
        cod="E06c", orden=11, area_responsable="BUCLE",
        nombre="Re-V°B° secuencial post-corrección [BUCLE]",
        es_bucle=True,
    ),
    "E07": EtapaSpec(
        cod="E07", orden=12, area_responsable="OEAS",
        nombre="Evaluación técnica (OEAS → OTIN)",
        campos_extra=("resultado_eval",),
        acepta_adjuntos=True,
    ),
    "E08": EtapaSpec(
        cod="E08", orden=13, area_responsable="OTIN",
        nombre="Respuesta OTIN a evaluación técnica (OTIN → OEAS)",
        campos_extra=("resultado_eval",),
        acepta_adjuntos=True,
    ),
    "E08a": EtapaSpec(
        cod="E08a", orden=14, area_responsable="BUCLE",
        nombre="Observaciones al proveedor [BUCLE] (OEAS → Prov.)",
        campos_extra=("motivo_bucle",),
        es_bucle=True,
    ),
    "E08b": EtapaSpec(
        cod="E08b", orden=15, area_responsable="BUCLE",
        nombre="Subsanación + re-evaluación [BUCLE] (Prov→OEAS→OTIN)",
        campos_extra=("motivo_bucle",),
        es_bucle=True,
    ),
    # ------------------------------------------------------------------
    # FASE 3 — Presupuesto y Certificación
    # ------------------------------------------------------------------
    "E09": EtapaSpec(
        cod="E09", orden=16, area_responsable="OEAS",
        nombre="Cuadro comparativo (OEAS → OTIN)",
        campos_extra=("monto_cert",),
        prerequisitos=("E08",),
        acepta_adjuntos=True,
    ),
    "E10": EtapaSpec(
        cod="E10", orden=17, area_responsable="OTIN",
        nombre="OTIN solicita anexo cert. + valida presupuesto (OTIN → Áreas)",
        campos_extra=("resultado_eval",),
    ),
    "E11": EtapaSpec(
        cod="E11", orden=18, area_responsable="AREAS",
        nombre="Solicitud cert. presupuestal (cada Área → OTIN)",
        campos_extra=("area_usuaria", "monto_cert"),
        por_area=True,
        acepta_adjuntos=True,
    ),
    "E12": EtapaSpec(
        cod="E12", orden=19, area_responsable="OTIN",
        nombre="Consolidación cert. presupuestales (OTIN)",
        prerequisitos=("E11",),
    ),
    "E13": EtapaSpec(
        cod="E13", orden=20, area_responsable="OTIN",
        nombre="Envío consolidado a Secretaría General (OTIN → SG)",
        acepta_adjuntos=True,
    ),
    "E14": EtapaSpec(
        cod="E14", orden=21, area_responsable="SEC_GENERAL",
        nombre="Aprobación Secretaría General (SG)",
        acepta_adjuntos=True,
    ),
    "E15": EtapaSpec(
        cod="E15", orden=22, area_responsable="SEC_GENERAL",
        nombre="Envío a OTPP (Sec. General → OTPP)",
        acepta_adjuntos=True,
    ),
    "E16": EtapaSpec(
        cod="E16", orden=23, area_responsable="OTPP",
        nombre="Certificación presupuestal — OTPP",
        campos_extra=("fecha_envio_otpp", "fecha_resp_otpp"),
        alerta_dias=20,
        acepta_adjuntos=True,
    ),
    # ------------------------------------------------------------------
    # FASE 4 — Orden y Ejecución
    # ------------------------------------------------------------------
    "E17": EtapaSpec(
        cod="E17", orden=24, area_responsable="OTPP",
        nombre="OTPP envía a OTA (OTPP → OTA)",
    ),
    "E18": EtapaSpec(
        cod="E18", orden=25, area_responsable="OTA",
        nombre="OTA deriva a OEAS (OTA → OEAS)",
    ),
    "E19": EtapaSpec(
        cod="E19", orden=26, area_responsable="OEAS",
        nombre="Emisión orden de compra/servicio (OEAS)",
        campos_extra=("nro_ocs", "monto_ocs", "plazo_entrega"),
        acepta_adjuntos=True,
    ),
    "E20": EtapaSpec(
        cod="E20", orden=27, area_responsable="OEAS",
        nombre="Notificación al proveedor (OEAS → Proveedor)",
        acepta_adjuntos=True,
    ),
    "E21": EtapaSpec(
        cod="E21", orden=28, area_responsable="PROVEEDOR",
        nombre="Confirmación recepción OCS (Proveedor→OEAS→OTIN)",
    ),
    "E22": EtapaSpec(
        cod="E22", orden=29, area_responsable="PROVEEDOR",
        nombre="Inicio de servicio / entrega del bien",
        acepta_adjuntos=True,
    ),
    # ------------------------------------------------------------------
    # FASE 5 — Conformidad
    # ------------------------------------------------------------------
    "E23": EtapaSpec(
        cod="E23", orden=30, area_responsable="OTIN",
        nombre="OTIN solicita conformidad (OTIN → Áreas)",
    ),
    "E24": EtapaSpec(
        cod="E24", orden=31, area_responsable="AREAS",
        nombre="Conformidad área usuaria [por área] (Áreas → OTIN)",
        campos_extra=("area_usuaria",),
        por_area=True,
        prerequisitos=("E23",),
        acepta_adjuntos=True,
    ),
    "E25": EtapaSpec(
        cod="E25", orden=32, area_responsable="OTIN",
        nombre="Conformidad final consolidada (OTIN) FIN",
        prerequisitos=("E24",),
        es_fin=True,
    ),
}

# ---------------------------------------------------------------------------
# C3c — Cadena principal secuencial (26 nodos; 6 bucles excluidos)
# Bucles: E05, E06, E06b, E06c, E08a, E08b (not in cadena)
# Each node gets its predecessor as prerequisito via dataclasses.replace.
# E01a is root (no prereq from chain — already has none).
# E01c prereq chain is special: E02 prereq is E01c (por_area), handled by
#   validaciones.py (all areas COMPLETADO/NO_APLICA gate), not just last row.
# ---------------------------------------------------------------------------

CADENA: tuple[str, ...] = (
    "E01a", "E01b", "E01c", "E02", "E02b", "E03", "E04",
    "E07", "E08", "E09", "E10", "E11", "E12",
    "E13", "E14", "E15", "E16", "E17", "E18", "E19", "E20", "E21", "E22",
    "E23", "E24", "E25",
)

# Verify cadena count: 32 total - 6 bucles (E05,E06,E06b,E06c,E08a,E08b) = 26 non-bucle stages
assert len(CADENA) == 26, f"CADENA must have 26 nodes, got {len(CADENA)}"

for _i in range(1, len(CADENA)):
    _cod, _prev = CADENA[_i], CADENA[_i - 1]
    ETAPAS_CATALOGO[_cod] = dataclasses.replace(
        ETAPAS_CATALOGO[_cod], prerequisitos=(_prev,)
    )

del _i, _cod, _prev  # clean up loop variables from module namespace

# Set of stage codes that accept file attachments (derived from acepta_adjuntos flag)
CODIGOS_CON_ADJUNTOS: frozenset[str] = frozenset(
    cod for cod, spec in ETAPAS_CATALOGO.items() if spec.acepta_adjuntos
)

# Canonical order list — used for sorting and progreso calculation.
# Bucle stages (E05/E06/E06b/E06c/E08a/E08b) appear interleaved by their orden value.
ORDEN_ETAPAS: list[str] = [
    spec.cod
    for spec in sorted(ETAPAS_CATALOGO.values(), key=lambda s: s.orden)
]

# Codes that are loop-type (excluded from progreso denominator)
_BUCLE_CODS: frozenset[str] = frozenset(
    cod for cod, spec in ETAPAS_CATALOGO.items() if spec.es_bucle
)

# Base denominator used by calcular_progreso: 26 (32 total - 6 bucles = 26 non-bucle).
# The actual runtime denominator is DYNAMIC: 26 minus the count of non-bucle stages
# marked NO_APLICA (see etapas_service.calcular_progreso). This constant is the
# maximum possible denominator (all stages applicable).
# Note: keeping alignment with v1 semantics — progreso is calculated as
# completadas / PROGRESO_DENOMINATOR. With 26 non-bucle stages, max = 26.
PROGRESO_DENOMINATOR: int = 26


# ---------------------------------------------------------------------------
# C4 — Fase groupings for the executive dashboard (5 business phases)
# Source of truth for backend; mirrored in frontend/src/lib/fases.ts.
# ---------------------------------------------------------------------------

FASES: dict[str, dict] = {
    "F1": {"orden": 1, "label": "Requerimiento y TDR"},
    "F2": {"orden": 2, "label": "Indagación y Evaluación"},
    "F3": {"orden": 3, "label": "Presupuesto y Certificación"},
    "F4": {"orden": 4, "label": "Orden y Ejecución"},
    "F5": {"orden": 5, "label": "Conformidad"},
}

COD_A_FASE: dict[str, str] = {
    "E01a": "F1", "E01b": "F1", "E01c": "F1", "E02": "F1", "E02b": "F1",
    "E03": "F2", "E04": "F2", "E05": "F2", "E06": "F2", "E06b": "F2", "E06c": "F2",
    "E07": "F2", "E08": "F2", "E08a": "F2", "E08b": "F2", "E09": "F2",
    "E10": "F3", "E11": "F3", "E12": "F3", "E13": "F3",
    "E14": "F3", "E15": "F3", "E16": "F3",
    "E17": "F4", "E18": "F4", "E19": "F4", "E20": "F4", "E21": "F4", "E22": "F4",
    "E23": "F5", "E24": "F5", "E25": "F5",
}


def fase_de_cod(cod: str) -> str:
    """Return the phase key (F1-F5) for a given stage code.

    Raises KeyError if the code is not in COD_A_FASE.
    """
    return COD_A_FASE[cod]


def get_etapa_spec(cod: str) -> EtapaSpec:
    """Return EtapaSpec for the given code. Raises KeyError if unknown."""
    return ETAPAS_CATALOGO[cod]


def siguiente_etapa_registrable(
    etapas_completadas_cods: list[str],
) -> str | None:
    """Return the first cod in ORDEN_ETAPAS not in etapas_completadas_cods.

    'Completadas' here means ALL rows for that cod are COMPLETADO
    (semantics enforced by calcular_progreso, not here).
    Returns None when the proceso has completed all stages.
    """
    completadas = set(etapas_completadas_cods)
    for cod in ORDEN_ETAPAS:
        if cod not in completadas:
            return cod
    return None
