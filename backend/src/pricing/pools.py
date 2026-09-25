"""
Pool Taxonomy

One definition per HKJC pool: which theta drives it, whether it settles on
the goal or the corner board, whether it is a first-half market, and the
baseline margin. Also holds the parsers that turn the raw feed's
``line_label`` and ``combination_string`` into something the resolvers in
grids.py can use.

The same pool is spelled three ways across the stack - the bet_type_code
enum in qfm_outbound_db says HILO, older scraper code says HIL, and the
desk says High/Low - so aliases are folded in here rather than at each
call site.

Change Log:
-----------
2026-08-30      Initialize
"""

import re
from dataclasses import dataclass
from typing import Optional

from src.core import config

# Canonical selection keys per pool kind.
THREE_WAY = ("H", "D", "A")
TWO_WAY_HA = ("H", "A")
TWO_WAY_HL = ("H", "L")
ODD_EVEN = ("O", "E")
HFT_KEYS = ("HH", "HD", "HA", "DH", "DD", "DA", "AH", "AD", "AA")


@dataclass(frozen=True)
class PoolDef:
    """Everything the pipeline needs to price and group one pool."""

    code: str
    name: str
    family: str
    domain: str          # goal | corner
    seg: str             # FT | HT  (HT = a first-half market)
    kind: str            # three_way | handicap_3w | asian_hcp | asian_total |
                         # odd_even | exact_total | correct_score | half_full
    has_line: bool
    settles_on_score: bool
    selections: tuple

    @property
    def margin(self) -> float:
        return config.POOL_MARGIN.get(self.code, config.DEFAULT_MARGIN)

    @property
    def optimized(self) -> bool:
        return self.code in config.OPTIMIZED_POOLS


POOLS = (
    PoolDef("HAD", "Home / Draw / Away", "Result", "goal", "FT",
            "three_way", False, True, THREE_WAY),
    PoolDef("FHAD", "First Half H/D/A", "Result", "goal", "HT",
            "three_way", False, True, THREE_WAY),
    PoolDef("HHAD", "Handicap H/D/A", "Result", "goal", "FT",
            "handicap_3w", True, True, THREE_WAY),
    PoolDef("HDC", "Handicap", "Handicap", "goal", "FT",
            "asian_hcp", True, True, TWO_WAY_HA),
    PoolDef("FHDC", "First Half Handicap", "Handicap", "goal", "HT",
            "asian_hcp", True, True, TWO_WAY_HA),
    PoolDef("HILO", "High / Low", "Totals", "goal", "FT",
            "asian_total", True, True, TWO_WAY_HL),
    PoolDef("FHLO", "First Half High / Low", "Totals", "goal", "HT",
            "asian_total", True, True, TWO_WAY_HL),
    PoolDef("OOE", "Odd / Even", "Totals", "goal", "FT",
            "odd_even", False, True, ODD_EVEN),
    PoolDef("TTG", "Total Goals", "Totals", "goal", "FT",
            "exact_total", False, True, tuple()),
    PoolDef("CRS", "Correct Score", "Score", "goal", "FT",
            "correct_score", False, True, tuple()),
    PoolDef("FCRS", "First Half Correct Score", "Score", "goal", "HT",
            "correct_score", False, True, tuple()),
    PoolDef("HFT", "Half Time / Full Time", "Score", "goal", "FT",
            "half_full", False, True, HFT_KEYS),
    PoolDef("CHLO", "Corner High / Low", "Corners", "corner", "FT",
            "asian_total", True, False, TWO_WAY_HL),
    PoolDef("CHDC", "Corner Handicap", "Corners", "corner", "FT",
            "asian_hcp", True, False, TWO_WAY_HA),
    PoolDef("FCHLO", "First Half Corner High / Low", "Corners", "corner", "HT",
            "asian_total", True, False, TWO_WAY_HL),
    PoolDef("FCHDC", "First Half Corner Handicap", "Corners", "corner", "HT",
            "asian_hcp", True, False, TWO_WAY_HA),
)

FAMILY_ORDER = ("Result", "Handicap", "Totals", "Score", "Corners", "Specials")

_BY_CODE = {p.code: p for p in POOLS}

# Feed spellings that mean a pool we already know.
_ALIAS = {
    "HIL": "HILO",
    "FHL": "FHLO",
    "CHL": "CHLO",
    "EHL": "CHLO",
    "ECH": "CHDC",
    "FCHL": "FCHLO",
    "HHA": "HHAD",
    "FHA": "FHAD",
    "HFTS": "HFT",
}

# Pools the feed returns that this MVP prices no view on. They still appear
# in the book, marked carried, so the E[GM] header cannot quietly overstate
# what the optimizer controls.
CARRIED_POOLS = (
    "FTS", "NTS", "TQL", "TPS", "FGS", "CHP", "GPF", "GPW", "SGA",
    "MSPC", "TSPC", "TNC", "JKC", "DHCP", "NGS", "LGS", "AGS",
)


def resolve(code) -> Optional[PoolDef]:
    """Look a pool up, tolerating the feed's alternative spellings."""
    if not code:
        return None
    c = str(code).strip().upper()
    return _BY_CODE.get(c) or _BY_CODE.get(_ALIAS.get(c, ""))


def is_extra_time(code) -> bool:
    """Extra-time pools are out of MVP scope but must not be mistaken for FT."""
    return str(code or "").strip().upper().startswith("ET")


# ---------------------------------------------------------------------------
# Line parsing
# ---------------------------------------------------------------------------

_BRACKET = re.compile(r"\[(.*?)\]")


def parse_line(label) -> Optional[float]:
    """Turn a feed line label into a signed float.

    Handles the plain form ("2.5", "-0.5"), the split form used for quarter
    lines ("1/1.5" -> 1.25, "-0.5/-1" -> -0.75) and the bracketed form that
    older extracts embed in the combination string ("H[-0.5]").
    """
    if label is None:
        return None
    text = str(label).strip()
    if not text or text.lower() in ("nan", "none"):
        return None

    m = _BRACKET.search(text)
    if m:
        text = m.group(1).strip()

    if "/" in text:
        parts = [p.strip() for p in text.split("/") if p.strip()]
        try:
            values = [float(p) for p in parts]
        except ValueError:
            return None
        return sum(values) / len(values) if values else None

    try:
        return float(text)
    except ValueError:
        return None


def line_label(value) -> str:
    """Render a parsed line back for display, keeping quarter lines readable."""
    if value is None:
        return ""
    quarter = (abs(value) * 4) % 2 != 0
    if not quarter:
        return "{:g}".format(value)
    lo, hi = value - 0.25, value + 0.25
    return "{:g}/{:g}".format(lo, hi)


# ---------------------------------------------------------------------------
# Selection parsing
# ---------------------------------------------------------------------------

_SCORE = re.compile(r"^\s*(\d{1,2})\s*[:\-]\s*(\d{1,2})\s*$")

_WORD_ALIASES = {
    "HOME": "H", "1": "H", "OVER": "H", "HIGH": "H",
    "AWAY": "A", "2": "A",
    "DRAW": "D", "X": "D", "TIE": "D",
    "UNDER": "L", "LOW": "L",
    "ODD": "O", "EVEN": "E",
    "OTHER": "OTHER", "AOS": "OTHER", "ANY": "OTHER",
}


def normalize_selection(pool: PoolDef, comb) -> Optional[str]:
    """Canonicalise ``combination_string`` for one pool.

    Returns None when the combination is not something this pool can price,
    which the caller records as an unpriced row rather than dropping.
    """
    if comb is None:
        return None
    text = str(comb).strip().upper()
    if not text or text in ("NAN", "NONE"):
        return None
    text = _BRACKET.sub("", text).strip()

    if pool.kind in ("correct_score",):
        m = _SCORE.match(text)
        if m:
            return "{}:{}".format(int(m.group(1)), int(m.group(2)))
        return "OTHER" if _WORD_ALIASES.get(text) == "OTHER" or text == "N" else None

    if pool.kind == "half_full":
        compact = text.replace("/", "").replace("-", "")
        if len(compact) == 2 and all(c in "HDA" for c in compact):
            return compact
        return None

    if pool.kind == "exact_total":
        plus = text.endswith("+")
        digits = re.sub(r"[^0-9]", "", text)
        if digits == "":
            return None
        return digits + "+" if plus else digits

    if pool.kind == "odd_even":
        key = _WORD_ALIASES.get(text, text)
        return key if key in ODD_EVEN else None

    key = _WORD_ALIASES.get(text, text[:1] if text else "")
    if pool.kind == "asian_total":
        return key if key in TWO_WAY_HL else None
    if pool.kind == "asian_hcp":
        # Some extracts label the under/low leg L on handicap pools.
        if key == "L":
            key = "A"
        return key if key in TWO_WAY_HA else None
    if pool.kind in ("three_way", "handicap_3w"):
        return key if key in THREE_WAY else None
    return None


def selection_label(pool: PoolDef, sel: str, home: str, away: str) -> str:
    """Human wording for a selection, used by the book tree."""
    if pool.kind == "asian_total":
        return "High" if sel == "H" else "Low"
    if pool.kind == "odd_even":
        return "Odd" if sel == "O" else "Even"
    if pool.kind == "half_full":
        names = {"H": home, "D": "Draw", "A": away}
        return "{} / {}".format(names.get(sel[0], sel[0]), names.get(sel[1], sel[1]))
    if pool.kind == "correct_score":
        return "Other" if sel == "OTHER" else sel
    if pool.kind == "exact_total":
        return "{} goals".format(sel)
    if sel == "H":
        return home
    if sel == "A":
        return away
    if sel == "D":
        return "Draw"
    return sel


def selection_key(match_id, pool_code, line_id, comb_id) -> str:
    """Stable identity for a book row across ticks."""
    return "{}:{}:{}:{}".format(match_id, pool_code, line_id, comb_id)
