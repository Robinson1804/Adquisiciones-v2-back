"""Normalizers for ingesta de correos documental — backend copy.

Decisión: el backend NO importa de tools/ (tools/ es local, no se despliega en Railway).
Esta es una copia explícita de tools/ingesta/normalizers.py para uso del service layer.
tools/ puede re-importar desde aquí o mantener su propia copia independiente.

Funciones puras, sin I/O, sin dependencias externas.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

# ---------------------------------------------------------------------------
# Constantes de umbral (ADR-5)
# ---------------------------------------------------------------------------

AUTO_LINK_THRESHOLD: float = 0.90   # auto-vinculación condicionada
SUGGESTION_THRESHOLD: float = 0.80  # sugerencias al revisor humano


# ---------------------------------------------------------------------------
# Patrones de oficio
# ---------------------------------------------------------------------------

_PAT_PREFIJO = re.compile(
    r"^\s*(?:oficio\s+)?n[°ºªo\.]*\s*",
    re.IGNORECASE,
)

_PAT_CEROS_IZQ = re.compile(r"^0+(?=\d)")

_PAT_SOLO_CEROS = re.compile(r"^0+$")


def normalizar_oficio(raw: str | None) -> str | None:
    """Normaliza el número de oficio a una forma canónica.

    Reglas:
    - None / vacío / solo ceros / solo espacios → None.
    - Strip del prefijo "OFICIO N°", "N°", "Nº", "Nª", "No.", etc.
    - Strip de ceros a la izquierda del número base (antes del primer guion).
    - Preservar año y sufijo institucional cuando existen.
    - Normalizar espacios alrededor de guiones.
    """
    if raw is None:
        return None

    s = raw.strip()

    if not s:
        return None

    s = _PAT_PREFIJO.sub("", s).strip()

    if not s:
        return None

    s = re.sub(r"\s*-\s*", "-", s)

    if _PAT_SOLO_CEROS.match(s):
        return None

    parts = s.split("-", 1)
    numero_base = _PAT_CEROS_IZQ.sub("", parts[0])

    if not numero_base:
        return None

    if len(parts) == 2:
        resultado = f"{numero_base}-{parts[1]}"
    else:
        resultado = numero_base

    if _PAT_SOLO_CEROS.match(resultado):
        return None

    return resultado


# ---------------------------------------------------------------------------
# Normalización de nombre de servicio
# ---------------------------------------------------------------------------

_STOPWORDS = (
    "servicio de ",
    "servicios de ",
    "contratacion de ",
    "contratación de ",
    "suscripcion a ",
    "suscripción a ",
    "adquisicion de ",
    "adquisición de ",
)

_PAT_PUNTUACION = re.compile(r"[^\w\s\-]")
_PAT_ESPACIOS_EXTRA = re.compile(r"\s+")


def _quitar_acentos(texto: str) -> str:
    """NFD → ASCII: elimina diacríticos."""
    nfd = unicodedata.normalize("NFD", texto)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def normalizar_nombre_servicio(nombre: str | None) -> str:
    """Normaliza un nombre de servicio para comparación difusa."""
    if not nombre:
        return ""

    s = nombre.lower()
    s = _quitar_acentos(s)

    stopwords_norm = sorted(
        (_quitar_acentos(sw.lower()) for sw in _STOPWORDS),
        key=len,
        reverse=True,
    )
    for sw in stopwords_norm:
        s = s.replace(sw, " ")

    s = _PAT_PUNTUACION.sub(" ", s)
    s = _PAT_ESPACIOS_EXTRA.sub(" ", s).strip()

    return s


def evaluar_similitud_nombre(a: str, b: str) -> float:
    """Evalúa la similitud entre dos nombres de servicio normalizados.

    Usa difflib.SequenceMatcher (stdlib). Retorna float en [0.0, 1.0].
    """
    return difflib.SequenceMatcher(None, a, b).ratio()
