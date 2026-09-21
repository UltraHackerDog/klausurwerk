"""Semester parsing. Unknown stays unknown — nothing here guesses."""
from __future__ import annotations

import re

UNKNOWN = "????"
UNKNOWN_LABEL = "Semester unbekannt"

_WS = re.compile(r"^WS(\d{4})/(\d{4})$")
_SS = re.compile(r"^SS(\d{4})$")
_SHORT = re.compile(r"^(\d{4})([ws])$")


def sort_key(semester: str | None) -> int | None:
    """Chronological integer for a canonical semester, else None.

    SS2019 -> 4038, WS2019/2020 -> 4039 (the winter term follows the summer term).
    """
    if not semester:
        return None
    m = _WS.match(semester)
    if m:
        return int(m.group(1)) * 2 + 1
    m = _SS.match(semester)
    if m:
        return int(m.group(1)) * 2
    return None


def from_short(short: str | None) -> str | None:
    """Convert the '2013w' / '2021s' notation used by the holiday-course
    archive into the canonical form. Pure notation change: '2013w' is the
    winter term that starts in 2013. Anything else returns None."""
    if not short:
        return None
    m = _SHORT.match(short.strip().lower())
    if not m:
        return None
    year = int(m.group(1))
    return f"WS{year}/{year + 1}" if m.group(2) == "w" else f"SS{year}"


def display(semester: str | None) -> str:
    if not semester or semester == UNKNOWN or sort_key(semester) is None:
        return UNKNOWN_LABEL
    m = _WS.match(semester)
    if m:
        return f"WS {m.group(1)}/{m.group(2)[2:]}"
    m = _SS.match(semester)
    return f"SS {m.group(1)}"


# --------------------------------------------------------------------------- #
# Free-text parsing for folder-scan mode
# --------------------------------------------------------------------------- #
# Only the explicit notations below are read. Anything else, and anything that
# can be read two ways, stays unknown. The matched substring is always reported.

import unicodedata
from dataclasses import dataclass

YEAR_MIN, YEAR_MAX = 1950, 2099
# Two-digit years follow the POSIX strptime convention: 00–68 -> 20xx, 69–99 -> 19xx.
TWO_DIGIT_PIVOT = 68

_TERM_PREFIX = re.compile(
    r"(?<![a-z])(wintersemester|wise|ws|sommersemester|sose|ss)[\s_.\-]*"
    r"(\d{4}|\d{2})(?:[\s_.\-/:]{0,3}(\d{4}|\d{2}))?(?!\d)"
)
_SHORT_FREE = re.compile(r"(?<![a-z0-9])(\d{4})([ws])(?![a-z0-9])")
_YEAR_RANGE = re.compile(r"(?<![a-z0-9])(\d{4})[\-_/](\d{4}|\d{2})(?!\d)")
_BARE_YEAR = re.compile(r"(?<![a-z0-9])(\d{4})(?!\d)")


@dataclass(frozen=True)
class Found:
    """Result of reading one name. At most one of `semester` / `year` is set."""
    semester: str | None = None      # canonical form
    year: int | None = None          # a bare year: year known, term unknown
    matched: str | None = None       # the substring that decided (or that is ambiguous)
    ambiguous: bool = False
    detail: str | None = None        # how a notation was read, when that needs saying
    spans: tuple[tuple[int, int], ...] = ()


def _plausible(year: int) -> bool:
    return YEAR_MIN <= year <= YEAR_MAX


def _full_year(digits: str) -> int:
    n = int(digits)
    if len(digits) == 4:
        return n
    return 2000 + n if n <= TWO_DIGIT_PIVOT else 1900 + n


def _follows(first: str, second: str) -> bool:
    """True when `second` names the year after `first` ('2021','22' / '21','22' / '99','00')."""
    a = _full_year(first)
    if len(second) == 4:
        return int(second) == a + 1
    return int(second) == (a + 1) % 100


def _winter(start: int) -> str:
    return f"WS{start}/{start + 1}"


def _read_prefixed(m: re.Match) -> tuple[set[str], str | None]:
    """Readings of one 'WS…' / 'SS…' match. More than one reading = ambiguous."""
    winter = m.group(1) in ("wintersemester", "wise", "ws")
    a, b = m.group(2), m.group(3)
    readings: set[str] = set()
    detail = None
    if winter:
        if b is not None and _follows(a, b):
            start = _full_year(a)
            if _plausible(start):
                readings.add(_winter(start))
            if len(a) == 2:
                detail = f"zweistellig „{a}“ als {start} gelesen"
            return readings, detail
        # A lone number: 'ws2122' is a pair, 'ws2019' is a year, 'ws2021' could be either.
        if len(a) == 4:
            if _follows(a[:2], a[2:]) and _plausible(_full_year(a[:2])):
                readings.add(_winter(_full_year(a[:2])))
            if _plausible(int(a)):
                readings.add(_winter(int(a)))
            if len(readings) == 1:
                detail = "als Wintersemester ab dem genannten Jahr gelesen" if _winter(int(a)) in readings \
                    else "als Jahrespaar gelesen"
        else:
            start = _full_year(a)
            if _plausible(start):
                readings.add(_winter(start))
                detail = f"zweistellig „{a}“ als Wintersemester ab {start} gelesen"
        return readings, detail
    # Summer term: one year. A following consecutive number looks like a winter range -> two readings.
    year = _full_year(a)
    if _plausible(year):
        readings.add(f"SS{year}")
        if len(a) == 2:
            detail = f"zweistellig „{a}“ als {year} gelesen"
    if b is not None and _follows(a, b):
        readings.add(_winter(year))
    return readings, detail


def parse_free(text: str | None) -> Found:
    """Read a semester out of a file or folder name. Explicit notations only:

      WS2021/22 · WS 2021-2022 · ws2122 · WiSe21 · Wintersemester 2021   -> winter term
      SS2019 · SoSe 2019 · ss19 · Sommersemester 2019                     -> summer term
      2021w / 2019s (archive notation) · 2021-2022 / 2021_22              -> winter / as written
      a bare year such as 2019                                             -> year known, term unknown

    Two different readings in one name (or 'ws2021', which is WS 2020/21 or WS 2021/22)
    are reported as ambiguous, never resolved. Digits glued to letters ('MA2003', a module
    code) are not read as a year.
    """
    if not text:
        return Found()
    original = unicodedata.normalize("NFC", text)
    low = original.lower()
    if len(low) != len(original):          # rare case-mapping that changes length
        original = low

    found: dict[str, tuple[int, int]] = {}
    spans: list[tuple[int, int]] = []
    details: list[str] = []
    ambiguous_at: tuple[int, int] | None = None
    for m in _TERM_PREFIX.finditer(low):
        readings, detail = _read_prefixed(m)
        if not readings:
            continue
        spans.append(m.span())
        if len(readings) > 1:
            ambiguous_at = m.span()
        for r in readings:
            found.setdefault(r, m.span())
        if detail:
            details.append(detail)
    for m in _SHORT_FREE.finditer(low):
        sem = from_short(m.group(0))
        if sem and _plausible(int(m.group(1))):
            found.setdefault(sem, m.span())
            spans.append(m.span())
            details.append("Archiv-Schreibweise umgerechnet")
    for m in _YEAR_RANGE.finditer(low):
        if any(s <= m.start() and m.end() <= e for s, e in spans):
            continue
        if _plausible(int(m.group(1))) and _follows(m.group(1), m.group(2)):
            found.setdefault(_winter(int(m.group(1))), m.span())
            spans.append(m.span())
            details.append("Jahrespaar als Wintersemester gelesen")

    if len(found) > 1 or ambiguous_at is not None:
        s, e = ambiguous_at or (min(v[0] for v in found.values()), max(v[1] for v in found.values()))
        return Found(matched=original[s:e], ambiguous=True,
                     detail="mehrere Lesarten: " + ", ".join(sorted(found)), spans=tuple(spans))

    years = {}
    for m in _BARE_YEAR.finditer(low):
        if any(s <= m.start() and m.end() <= e for s, e in spans):
            continue
        if _plausible(int(m.group(1))):
            years.setdefault(int(m.group(1)), m.span())

    if found:
        sem, (s, e) = next(iter(found.items()))
        if any(not year_fits(sem, y) for y in years):
            return Found(matched=original[s:e], ambiguous=True, spans=tuple(spans),
                         detail=f"{sem} passt nicht zur Jahreszahl {', '.join(str(y) for y in sorted(years))}")
        return Found(semester=sem, matched=original[s:e], detail="; ".join(dict.fromkeys(details)) or None,
                     spans=tuple(spans))
    if len(years) == 1:
        year, (s, e) = next(iter(years.items()))
        return Found(year=year, matched=original[s:e])
    if len(years) > 1:
        return Found(matched=", ".join(str(y) for y in sorted(years)), ambiguous=True,
                     detail="mehrere Jahreszahlen")
    return Found()


def year_fits(semester: str, year: int) -> bool:
    """Whether a bare year can belong to a canonical semester (a winter term spans two years)."""
    m = _WS.match(semester)
    if m:
        return year in (int(m.group(1)), int(m.group(2)))
    m = _SS.match(semester)
    return bool(m) and year == int(m.group(1))


def canonical(text: str | None) -> str | None:
    """Canonical semester for a hand-written value (sidecar): either already canonical, or one
    unambiguous explicit notation covering a term. None otherwise."""
    text = (text or "").strip()
    if sort_key(text) is not None:
        return text
    found = parse_free(text)
    return found.semester if found.semester and not found.ambiguous else None
