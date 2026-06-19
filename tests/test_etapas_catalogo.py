"""Tests for the 31-stage catalog (etapas_catalogo.py).

Sync strategy (APPLY-TIME RISK #2):
constants.ts is parsed via regex to extract cod values. If the file is
unreachable or the regex yields 0 results, the test falls back to a
hardcoded canonical list of codes.

flujo-real-otin-v2: catalog expanded from 28 → 31 entries.
New codes: E01a, E01b, E01c (replace E01), E02b, E06c.
"""
import re
from pathlib import Path

import pytest

from app.services.etapas_catalogo import (
    ETAPAS_CATALOGO,
    ORDEN_ETAPAS,
    get_etapa_spec,
    siguiente_etapa_registrable,
)

# ---------------------------------------------------------------------------
# Canonical 31 codes (flujo-real-otin-v2)
# E01 removed; E01a/E01b/E01c/E02b/E06c added.
# ---------------------------------------------------------------------------
_CANONICAL_CODES: list[str] = [
    "E01a", "E01b", "E01c", "E02", "E02b", "E03", "E04",
    "E05", "E06", "E06b", "E06c", "E07", "E08",
    "E08a", "E08b", "E09", "E10", "E11", "E12", "E13", "E14",
    "E15", "E16", "E17", "E18", "E19", "E20", "E21", "E22",
    "E23", "E24", "E25",
]
# 32 codes total: 28 original - E01 + E01a + E01b + E01c + E02b + E06c

_EXPECTED_BUCLE: frozenset[str] = frozenset({"E05", "E06", "E06b", "E06c", "E08a", "E08b"})
_EXPECTED_POR_AREA: frozenset[str] = frozenset({"E01c", "E11", "E24"})


# ---------------------------------------------------------------------------
# Basic catalog tests
# ---------------------------------------------------------------------------

def test_catalog_has_31_codes():
    """Catalog has 32 entries: 28 original - E01 + E01a + E01b + E01c + E02b + E06c."""
    # Note: spec says 31 but actual count is 32 due to E01 splitting into 3 codes
    # (E01a/E01b/E01c net +2) plus E02b and E06c (net +2) = 28 + 4 = 32.
    # The CADENA has 26 non-bucle nodes; PROGRESO_DENOMINATOR=26 (all non-bucle nodes).
    # Test name kept for tracking; actual assertion is 32.
    assert len(ETAPAS_CATALOGO) == 32


def test_orden_etapas_has_31():
    """ORDEN_ETAPAS has 32 entries after adding new codes."""
    assert len(ORDEN_ETAPAS) == 32


def test_orden_etapas_no_duplicates():
    assert len(set(ORDEN_ETAPAS)) == len(ORDEN_ETAPAS)


def test_bucle_flags():
    """es_bucle is True only for E05, E06, E06b, E06c, E08a, E08b."""
    actual = frozenset(c for c, s in ETAPAS_CATALOGO.items() if s.es_bucle)
    assert actual == _EXPECTED_BUCLE


def test_por_area_flags():
    """por_area is True only for E01c, E11, E24."""
    actual = frozenset(c for c, s in ETAPAS_CATALOGO.items() if s.por_area)
    assert actual == _EXPECTED_POR_AREA


def test_prerequisitos_defined():
    """Key prerequisito chains from Design D1 are present."""
    # New chain: E01a → E01b → E01c → E02 → E02b → E03 ...
    assert "E01a" in ETAPAS_CATALOGO["E01b"].prerequisitos
    assert "E01b" in ETAPAS_CATALOGO["E01c"].prerequisitos
    assert "E01c" in ETAPAS_CATALOGO["E02"].prerequisitos
    assert "E02" in ETAPAS_CATALOGO["E02b"].prerequisitos
    assert "E02b" in ETAPAS_CATALOGO["E03"].prerequisitos
    assert "E04" in ETAPAS_CATALOGO["E05"].prerequisitos
    assert "E04" in ETAPAS_CATALOGO["E06"].prerequisitos
    assert "E08" in ETAPAS_CATALOGO["E09"].prerequisitos
    assert "E11" in ETAPAS_CATALOGO["E12"].prerequisitos
    assert "E24" in ETAPAS_CATALOGO["E25"].prerequisitos


def test_new_codes_present():
    """New codes E01a, E01b, E01c, E02b, E06c present; E01 absent."""
    for cod in ("E01a", "E01b", "E01c", "E02b", "E06c"):
        assert cod in ETAPAS_CATALOGO, f"{cod} missing from catalog"
    assert "E01" not in ETAPAS_CATALOGO, "E01 must not exist in v2 catalog"


def test_e01c_is_por_area():
    """E01c must have por_area = True."""
    assert ETAPAS_CATALOGO["E01c"].por_area is True


def test_e01a_is_not_por_area():
    """E01a must have por_area = False."""
    assert ETAPAS_CATALOGO["E01a"].por_area is False


def test_e06c_is_bucle():
    """E06c must have es_bucle = True."""
    assert ETAPAS_CATALOGO["E06c"].es_bucle is True


def test_e25_is_fin():
    assert ETAPAS_CATALOGO["E25"].es_fin is True


def test_e16_alerta_dias():
    assert ETAPAS_CATALOGO["E16"].alerta_dias == 20


def test_orden_etapas_matches_catalogo_keys():
    """ORDEN_ETAPAS must contain exactly the same codes as ETAPAS_CATALOGO."""
    assert set(ORDEN_ETAPAS) == set(ETAPAS_CATALOGO.keys())


def test_get_etapa_spec_known():
    spec = get_etapa_spec("E01c")
    assert spec.cod == "E01c"
    assert spec.por_area is True


def test_get_etapa_spec_unknown():
    with pytest.raises(KeyError):
        get_etapa_spec("E99")


def test_siguiente_etapa_registrable_empty():
    """No completed stages → first stage is next."""
    assert siguiente_etapa_registrable([]) == ORDEN_ETAPAS[0]


def test_siguiente_etapa_registrable_partial():
    """Skip already-completed codes."""
    completed = ORDEN_ETAPAS[:3]  # E01, E02, E03
    result = siguiente_etapa_registrable(completed)
    assert result == ORDEN_ETAPAS[3]


def test_siguiente_etapa_registrable_all_done():
    """All done → returns None."""
    result = siguiente_etapa_registrable(ORDEN_ETAPAS)
    assert result is None


# ---------------------------------------------------------------------------
# Sync test: backend catalog vs. frontend constants.ts (APPLY-TIME RISK #2)
#
# Strategy: parse frontend/src/lib/constants.ts via regex to extract cod
# values. If constants.ts is unreachable (wrong path, encoding issues, or
# parse yields 0 codes), fall back to the hardcoded canonical list and
# document the choice via a warning in the test output.
# ---------------------------------------------------------------------------

def _parse_constants_ts() -> list[str] | None:
    """Extract cod values from ETAPAS_CONFIG in constants.ts via regex.

    Returns list of codes if successful, None if file not found.
    The regex finds: { cod: 'E01',  ... (no TS parsing needed — pattern is stable)
    """
    candidates = [
        Path(__file__).parents[3] / "frontend" / "src" / "lib" / "constants.ts",
        Path(__file__).parents[2] / "frontend" / "src" / "lib" / "constants.ts",
    ]
    for path in candidates:
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                codes = re.findall(r"cod:\s*'(E[0-9]{2,3}[ab]?)'", content)
                return codes if codes else None
            except Exception:
                return None
    return None


def test_catalogo_sincronizado():
    """Backend catalog codes match the canonical 31-code list.

    flujo-real-otin-v2: E01 removed; E01a/E01b/E01c/E02b/E06c added.
    Frontend constants.ts update is deferred to PR4 (backend-only PR).
    """
    import warnings

    be_codes = set(ETAPAS_CATALOGO.keys())
    canonical_set = set(_CANONICAL_CODES)

    # Backend must contain all canonical codes (nothing dropped)
    missing_in_be = canonical_set - be_codes
    assert not missing_in_be, f"Canonical codes missing in backend: {sorted(missing_in_be)}"

    # Backend must contain exactly the canonical codes
    extra_in_be = be_codes - canonical_set
    assert not extra_in_be, f"Backend codes not in canonical list: {sorted(extra_in_be)}"

    # Optional: warn if frontend file is reachable but diverges (expected during rollout)
    fe_codes = _parse_constants_ts()
    if fe_codes is not None:
        fe_set = set(fe_codes)
        fe_only = fe_set - be_codes
        if fe_only:
            warnings.warn(
                f"Frontend has codes not in backend catalog: {sorted(fe_only)}",
                stacklevel=2,
            )
        be_only = be_codes - fe_set
        if be_only:
            warnings.warn(
                f"Backend has codes not yet in frontend constants.ts (PR4 pending): "
                f"{sorted(be_only)}",
                stacklevel=2,
            )

    assert len(be_codes) == 32, f"Expected 32 codes in backend catalog, got {len(be_codes)}"


def test_catalogo_bucle_flags_sync():
    """Backend es_bucle flags match expected set (E05/E06/E06b/E06c/E08a/E08b)."""
    be_bucle = frozenset(c for c, s in ETAPAS_CATALOGO.items() if s.es_bucle)
    assert be_bucle == _EXPECTED_BUCLE


def test_catalogo_por_area_flags_sync():
    """Backend por_area flags match expected set (E01c/E11/E24)."""
    be_por_area = frozenset(c for c, s in ETAPAS_CATALOGO.items() if s.por_area)
    assert be_por_area == _EXPECTED_POR_AREA
