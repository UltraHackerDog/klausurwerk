"""Folder-scan mode: build the catalogue from a plain folder of PDFs.

Nothing is invented. Every derived value records how it was derived
(folder / filename / sidecar / default), and what cannot be derived stays
unknown. The collection is only ever read.

Derivation rules live in the data tables at the top of this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import semesters
from .records import (FROM_COMPUTED, FROM_DEFAULT, FROM_FILENAME, FROM_FOLDER, FROM_SIDECAR, EntryRec, FileRec)

ORIGIN = "folder-scan"
SIDECAR_NAME = "klausurwerk.json"
OCR_DIR = "_ocr"
UNSORTED_SUBJECT = "Unsortiert"
DEFAULT_MAX_FILES = 50_000
MAX_DEPTH = 16                      # folders below the root; deeper trees are reported, not walked
MAX_SIDECAR_BYTES = 1_000_000
KINDS = ("exam", "exercise", "material")

# Kind keywords. "part" keywords match anywhere inside a name (German compounds:
# "Probeklausur", "Übungsblatt"); "word" keywords must stand alone as a word,
# optionally with a plural s ("exam" must not fire on "example", "test" not on "Testat").
KIND_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "exam": {
        "part": ("klausur", "probeklausur", "nachklausur", "wiederholungsklausur", "semestrale",
                 "pruefung", "prüfung"),
        "word": ("exam", "midterm", "final", "mock", "test"),
    },
    "exercise": {
        "part": ("uebung", "übung", "blatt", "homework", "hausaufgabe", "tutorium"),
        "word": ("sheet", "exercise", "tutorial", "problem set"),
    },
    "material": {
        "part": ("skript", "notizen", "zusammenfassung", "formelsammlung", "cheatsheet", "folien"),
        "word": ("script", "notes", "summary", "slides"),
    },
}

# Solution / question markers: a whole name segment between separators (_ - space .).
ROLE_MARKERS: dict[str, str] = {
    "loesung": "loesung", "lösung": "loesung", "loesungen": "loesung", "lösungen": "loesung",
    "lsg": "loesung", "lsgn": "loesung", "sol": "loesung", "solution": "loesung", "solutions": "loesung",
    "musterloesung": "loesung", "musterlösung": "loesung", "ml": "loesung",
    "angabe": "angabe", "aufgaben": "angabe",
}
# Short markers that are also ordinary abbreviations ("ML" = machine learning) only count as the
# last segment of a longer name.
SUFFIX_ONLY_MARKERS = frozenset({"ml", "sol"})

_SEPARATORS = re.compile(r"([_\-\s.]+)")
_WORDS = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)
_CONFLICT_COPY = re.compile(r"^(.+) ([2-9])$")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


# --------------------------------------------------------------------------- #
# Derivation: kind
# --------------------------------------------------------------------------- #

def kinds_in(name: str) -> dict[str, str]:
    """{kind: keyword that matched} for one file or folder name."""
    low = nfc(name).lower()
    words = " " + " ".join(_WORDS.findall(low)) + " "
    hits: dict[str, str] = {}
    for kind, table in KIND_KEYWORDS.items():
        for kw in table["part"]:
            if kw in low:
                hits.setdefault(kind, kw)
        for kw in table["word"]:
            if f" {kw} " in words or f" {kw}s " in words:
                hits.setdefault(kind, kw)
    return hits


def derive_kind(stem: str, folders_nearest_first: list[str]) -> tuple[str, str, str | None]:
    """(kind, derived_from, note). The file name decides first, then the nearest folder that
    says anything. A name that points at two kinds is not resolved."""
    levels = [(FROM_FILENAME, stem)] + [(FROM_FOLDER, f) for f in folders_nearest_first]
    for origin, name in levels:
        hits = kinds_in(name)
        if not hits:
            continue
        where = "Dateinamen" if origin == FROM_FILENAME else f"Ordner „{nfc(name)}“"
        if len(hits) > 1:
            listed = ", ".join(f"„{kw}“" for kw in hits.values())
            return "material", FROM_DEFAULT, f"Art nicht erkannt: mehrere Stichwörter im {where} ({listed})"
        kind, kw = next(iter(hits.items()))
        return kind, origin, f"Stichwort „{kw}“ im {where}"
    return "material", FROM_DEFAULT, "Art nicht erkannt – als Material geführt"


# --------------------------------------------------------------------------- #
# Derivation: semester
# --------------------------------------------------------------------------- #

def derive_semester(stem: str, folders_nearest_first: list[str]) -> tuple[str, str | None, str | None, str | None]:
    """(semester, semester_raw, derived_from, note). A term written out wins over a bare year;
    the nearest name wins; anything ambiguous or contradictory stays unknown."""
    levels = [(FROM_FILENAME, stem)] + [(FROM_FOLDER, f) for f in folders_nearest_first]
    read = [(origin, name, semesters.parse_free(name)) for origin, name in levels]

    def where(origin: str, name: str) -> str:
        return "Dateinamen" if origin == FROM_FILENAME else f"Ordner „{nfc(name)}“"

    nearer_years: list[int] = []
    for origin, name, found in read:
        if found.ambiguous:
            return (semesters.UNKNOWN, found.matched, None,
                    f"„{found.matched}“ im {where(origin, name)} ist nicht eindeutig ({found.detail}) – nicht zugeordnet")
        if found.semester:
            clash = [y for y in nearer_years if not semesters.year_fits(found.semester, y)]
            if clash:
                return (semesters.UNKNOWN, found.matched, None,
                        f"„{found.matched}“ im {where(origin, name)} passt nicht zur Jahreszahl {clash[0]} – nicht zugeordnet")
            note = f"aus „{found.matched}“ im {where(origin, name)}"
            if found.detail:
                note += f" ({found.detail})"
            return found.semester, found.matched, origin, note
        if found.year is not None:
            nearer_years.append(found.year)
    for origin, name, found in read:
        if found.year is not None:
            return (semesters.UNKNOWN, found.matched, None,
                    f"Jahr {found.year} im {where(origin, name)}, Semester nicht genannt")
    return semesters.UNKNOWN, None, None, None


# --------------------------------------------------------------------------- #
# Derivation: role, pairing key, title, id
# --------------------------------------------------------------------------- #

def split_role(stem: str) -> tuple[str, str, str | None]:
    """(base, role, marker). `base` is the stem without solution/question markers, lower-cased:
    two files in one folder with the same base belong to one entry."""
    parts = _SEPARATORS.split(nfc(stem))
    segments = [i for i in range(0, len(parts), 2) if parts[i]]
    drop: list[int] = []
    roles: list[tuple[str, str]] = []
    for i in segments:
        low = parts[i].lower()
        role = ROLE_MARKERS.get(low)
        if role is None:
            continue
        if low in SUFFIX_ONLY_MARKERS and (i != segments[-1] or len(segments) == 1):
            continue
        drop.append(i)
        roles.append((role, parts[i]))
    if not roles:
        return nfc(stem).lower(), "angabe", None
    for i in drop:
        parts[i] = ""
        if i - 1 >= 0 and parts[i - 1]:
            parts[i - 1] = ""              # the separator in front of the marker
        elif i + 1 < len(parts):
            parts[i + 1] = ""              # a leading marker: the separator behind it
    base = "".join(parts).strip("_-. ").lower()
    solution = [m for r, m in roles if r == "loesung"]
    if solution:
        return base, "loesung", solution[0]
    return base, "angabe", roles[0][1]


def readable_title(stem: str) -> str:
    """The file stem with underscores as spaces. Nothing else is changed."""
    return re.sub(r"\s+", " ", nfc(stem).replace("_", " ")).strip() or nfc(stem)


def slug(text: str) -> str:
    text = nfc(text).lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def entry_id(key: str) -> str:
    """Deterministic id: readable slug of the pairing key plus a short hash of it.
    The key is the relative path without extension and without solution marker, so a
    paper and its solution share it. Renaming or moving a file changes the id."""
    digest = hashlib.sha1(nfc(key).encode("utf-8")).hexdigest()[:8]
    tail = slug(key)[-64:].strip("-") or "x"
    return f"{tail}-{digest}"


# --------------------------------------------------------------------------- #
# Sidecar
# --------------------------------------------------------------------------- #

class SidecarError(ValueError):
    pass


@dataclass
class Sidecar:
    rel_path: str
    defaults: dict = field(default_factory=dict)
    files: dict[str, dict] = field(default_factory=dict)


def _text(value, what: str, limit: int = 300) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SidecarError(f"{what}: expected a non-empty string of at most {limit} characters")
    return nfc(value.strip())


def _kind(value, what: str) -> str:
    if value not in KINDS:
        raise SidecarError(f"{what}: kind must be one of {', '.join(KINDS)}")
    return value


def _semester(value, what: str) -> tuple[str, str]:
    raw = _text(value, what, 40)
    sem = semesters.canonical(raw)
    if sem is None:
        raise SidecarError(f"{what}: semester „{raw}“ is not a recognised term (use e.g. WS2021/2022 or SS2019)")
    return sem, raw


def parse_sidecar(raw_text: str, rel_path: str, pdf_names: set[str]) -> Sidecar:
    """Strict validation. Any problem rejects the whole file: a sidecar is never half-applied."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SidecarError(f"not valid JSON ({exc.msg}, line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise SidecarError("expected a JSON object")
    unknown = set(data) - {"defaults", "files"}
    if unknown:
        raise SidecarError(f"unknown key(s): {', '.join(sorted(map(str, unknown)))}")

    out = Sidecar(rel_path=rel_path)
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise SidecarError("defaults: expected an object")
    unknown = set(defaults) - {"subject", "kind", "semester", "lecturers"}
    if unknown:
        raise SidecarError(f"defaults: unknown key(s): {', '.join(sorted(map(str, unknown)))}")
    if "subject" in defaults:
        out.defaults["subject"] = _text(defaults["subject"], "defaults.subject", 200)
    if "kind" in defaults:
        out.defaults["kind"] = _kind(defaults["kind"], "defaults.kind")
    if "semester" in defaults:
        out.defaults["semester"] = _semester(defaults["semester"], "defaults.semester")
    if "lecturers" in defaults:
        names = defaults["lecturers"]
        if not isinstance(names, list) or len(names) > 20:
            raise SidecarError("defaults.lecturers: expected a list of at most 20 names")
        out.defaults["lecturers"] = [_text(n, "defaults.lecturers[]", 200) for n in names]

    files = data.get("files", {})
    if not isinstance(files, dict):
        raise SidecarError("files: expected an object keyed by file name")
    known = {nfc(n): n for n in pdf_names}
    for name, spec in files.items():
        what = f"files[{name!r}]"
        if nfc(str(name)) not in known:
            raise SidecarError(f"{what}: no such PDF in this folder")
        if not isinstance(spec, dict):
            raise SidecarError(f"{what}: expected an object")
        unknown = set(spec) - {"title", "kind", "semester", "solution_of"}
        if unknown:
            raise SidecarError(f"{what}: unknown key(s): {', '.join(sorted(map(str, unknown)))}")
        clean: dict = {}
        if "title" in spec:
            clean["title"] = _text(spec["title"], f"{what}.title")
        if "kind" in spec:
            clean["kind"] = _kind(spec["kind"], f"{what}.kind")
        if "semester" in spec:
            clean["semester"] = _semester(spec["semester"], f"{what}.semester")
        if "solution_of" in spec:
            target = spec["solution_of"]
            if target is None:
                clean["solution_of"] = None            # "this file is not a solution"
            else:
                if not isinstance(target, str) or nfc(target) not in known:
                    raise SidecarError(f"{what}.solution_of: no such PDF in this folder")
                if nfc(target) == nfc(str(name)):
                    raise SidecarError(f"{what}.solution_of: a file cannot be its own solution")
                if set(spec) - {"solution_of"}:
                    raise SidecarError(f"{what}: a solution takes title, kind and semester from its paper")
                clean["solution_of"] = known[nfc(target)]
        out.files[known[nfc(str(name))]] = clean
    targets = [s["solution_of"] for s in out.files.values() if s.get("solution_of")]
    if len(targets) != len(set(targets)):
        raise SidecarError("files: two files claim to be the solution of the same paper")
    for t in targets:
        if out.files.get(t, {}).get("solution_of"):
            raise SidecarError(f"files[{t!r}]: is named as a paper and as a solution")
    return out


# --------------------------------------------------------------------------- #
# The scan
# --------------------------------------------------------------------------- #

@dataclass
class _Pdf:
    name: str
    rel_path: str
    full: Path
    size: int
    mtime_ns: int
    sha256: str | None = None


def new_report(max_files: int) -> dict:
    return {
        "max_files": max_files, "cap_hit": False, "files_seen": 0, "pdfs_catalogued": 0,
        "other_files": 0, "other_extensions": {}, "skipped_hidden": 0, "skipped_symlinks": 0,
        "skipped_identical_conflict_copies": 0, "skipped_too_deep": 0, "unreadable": 0,
        "sha256_computed": 0, "sha256_reused": 0,
        "sidecars_applied": 0, "sidecars_rejected": [],
        "entries": 0, "entries_paired": 0, "solutions_without_paper": 0,
        "kind_not_recognised": 0, "semester_unknown": 0,
    }


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def scan_collection(root: Path, max_files: int = DEFAULT_MAX_FILES,
                    known: dict[str, tuple[int | None, int | None, str | None]] | None = None,
                    ) -> tuple[list[EntryRec], dict]:
    """Walk `root` and return (entries, report). `known` maps rel_path to the
    (bytes, mtime_ns, sha256) of the previous scan so unchanged files are not re-hashed.

    Symlinks are never followed (files or folders), hidden names and the top-level
    `_ocr/` are skipped, the walk stops at `max_files` files and says so."""
    root = root.resolve()
    known = known or {}
    report = new_report(max_files)
    entries: list[EntryRec] = []

    def fingerprint(pdf: _Pdf) -> str:
        if pdf.sha256 is None:
            before = known.get(pdf.rel_path)
            if before and before[0] == pdf.size and before[1] == pdf.mtime_ns and before[2]:
                pdf.sha256 = before[2]
                report["sha256_reused"] += 1
            else:
                pdf.sha256 = sha256_of(pdf.full)
                report["sha256_computed"] += 1
        return pdf.sha256

    def walk(folder: Path, rel_parts: tuple[str, ...], inherited: dict) -> None:
        if report["cap_hit"]:
            return
        try:
            with os.scandir(folder) as it:
                listing = sorted(it, key=lambda e: e.name)
        except OSError:
            report["unreadable"] += 1
            return
        pdfs: list[_Pdf] = []
        subdirs: list[os.DirEntry] = []
        sidecar_entry: os.DirEntry | None = None
        for item in listing:
            if item.name.startswith("."):
                report["skipped_hidden"] += 1
                continue
            if item.is_symlink():
                report["skipped_symlinks"] += 1
                continue
            if item.is_dir(follow_symlinks=False):
                if not rel_parts and item.name == OCR_DIR:
                    continue
                subdirs.append(item)
                continue
            if not item.is_file(follow_symlinks=False):
                continue
            if report["files_seen"] >= max_files:
                report["cap_hit"] = True          # what was listed so far is still catalogued
                break
            report["files_seen"] += 1
            if item.name == SIDECAR_NAME:
                sidecar_entry = item
                continue
            if not item.name.lower().endswith(".pdf"):
                report["other_files"] += 1
                ext = Path(item.name).suffix.lower() or "(none)"
                report["other_extensions"][ext] = report["other_extensions"].get(ext, 0) + 1
                continue
            try:
                st = item.stat(follow_symlinks=False)
            except OSError:
                report["unreadable"] += 1
                continue
            pdfs.append(_Pdf(name=item.name, rel_path="/".join(rel_parts + (item.name,)),
                             full=Path(item.path), size=st.st_size, mtime_ns=st.st_mtime_ns))

        # "paper 2.pdf" next to "paper.pdf": a sync conflict copy only if it is byte-identical.
        by_name = {p.name: p for p in pdfs}
        kept: list[_Pdf] = []
        for pdf in pdfs:
            stem, ext = os.path.splitext(pdf.name)
            m = _CONFLICT_COPY.match(stem)
            original = by_name.get(m.group(1) + ext) if m else None
            try:
                if original is not None and original.size == pdf.size and fingerprint(original) == fingerprint(pdf):
                    report["skipped_identical_conflict_copies"] += 1
                    continue
                fingerprint(pdf)
            except OSError:
                report["unreadable"] += 1
                continue
            kept.append(pdf)

        here = dict(inherited)
        sidecar: Sidecar | None = None
        if sidecar_entry is not None:
            rel = "/".join(rel_parts + (SIDECAR_NAME,))
            try:
                if sidecar_entry.stat(follow_symlinks=False).st_size > MAX_SIDECAR_BYTES:
                    raise SidecarError("file is too large")
                text = Path(sidecar_entry.path).read_text(encoding="utf-8-sig")
                sidecar = parse_sidecar(text, rel, {p.name for p in kept})
            except (SidecarError, OSError, UnicodeDecodeError) as exc:
                report["sidecars_rejected"].append({"file": rel, "error": str(exc)[:300]})
            else:
                report["sidecars_applied"] += 1
                for key, value in sidecar.defaults.items():
                    here[key] = (value, rel)

        entries.extend(_entries_for_folder(rel_parts, kept, here, sidecar))
        report["pdfs_catalogued"] += len(kept)

        for sub in subdirs:
            if len(rel_parts) + 1 > MAX_DEPTH:
                report["skipped_too_deep"] += 1
                continue
            walk(Path(sub.path), rel_parts + (sub.name,), here)

    walk(root, (), {})

    report["entries"] = len(entries)
    report["entries_paired"] = sum(1 for e in entries if len(e.files) == 2)
    report["solutions_without_paper"] = sum(1 for e in entries if all(f.role == "loesung" for f in e.files))
    report["kind_not_recognised"] = sum(1 for e in entries if e.kind_from == FROM_DEFAULT)
    report["semester_unknown"] = sum(1 for e in entries if e.semester == semesters.UNKNOWN)
    return entries, report


def _entries_for_folder(rel_parts: tuple[str, ...], pdfs: list[_Pdf], defaults: dict,
                        sidecar: Sidecar | None) -> list[EntryRec]:
    rel_dir = "/".join(rel_parts)
    specs = sidecar.files if sidecar else {}
    sidecar_rel = sidecar.rel_path if sidecar else None
    by_name = {p.name: p for p in pdfs}

    # 1. role and pairing key per file
    plan: dict[str, tuple[str, str, str, str | None]] = {}     # name -> (key, role, role_from, note)
    forced_papers = {s["solution_of"] for s in specs.values() if s.get("solution_of")}
    for pdf in pdfs:
        stem = os.path.splitext(pdf.name)[0]
        base, role, marker = split_role(stem)
        spec = specs.get(pdf.name, {})
        if "solution_of" in spec and spec["solution_of"] is None:
            plan[pdf.name] = (nfc(stem).lower(), "angabe", FROM_SIDECAR, f"laut {sidecar_rel} keine Lösung")
        elif pdf.name in forced_papers and role == "loesung":
            plan[pdf.name] = (nfc(stem).lower(), "angabe", FROM_SIDECAR, f"laut {sidecar_rel} die Angabe")
        else:
            note = f"Kennzeichen „{marker}“ im Dateinamen" if marker else "kein Lösungskennzeichen im Dateinamen"
            plan[pdf.name] = (base, role, FROM_FILENAME, note)
    for name, spec in specs.items():
        target = spec.get("solution_of")
        if target:
            plan[name] = (plan[target][0], "loesung", FROM_SIDECAR, f"laut {sidecar_rel} Lösung zu „{nfc(target)}“")

    # 2. group; one file per role, extras stand alone
    groups: dict[str, dict[str, list[_Pdf]]] = {}
    for pdf in pdfs:
        key, role, _, _ = plan[pdf.name]
        groups.setdefault(key, {"angabe": [], "loesung": []})[role].append(pdf)

    def rank(pdf: _Pdf) -> tuple:
        key, _, role_from, _ = plan[pdf.name]
        exact = os.path.splitext(nfc(pdf.name))[0].lower() == key
        return (role_from != FROM_SIDECAR, not exact, pdf.name)

    out: list[EntryRec] = []
    for key, roles in groups.items():
        members: list[_Pdf] = []
        extras: list[_Pdf] = []
        for role in ("angabe", "loesung"):
            ordered = sorted(roles[role], key=rank)
            members += ordered[:1]
            extras += ordered[1:]
        out.append(_build_entry(rel_parts, f"{rel_dir}/{key}" if rel_dir else key, members, plan, defaults, specs, sidecar_rel))
        for pdf in extras:
            rec = _build_entry(rel_parts, f"file:{pdf.rel_path}", [pdf], plan, defaults, specs, sidecar_rel)
            rec.files[0].role_note = (rec.files[0].role_note or "") + " · gleicher Name mehrfach im Ordner, eigener Eintrag"
            out.append(rec)
    return out


def _build_entry(rel_parts: tuple[str, ...], key: str, members: list[_Pdf], plan: dict, defaults: dict,
                 specs: dict[str, dict], sidecar_rel: str | None) -> EntryRec:
    primary = members[0]                                  # the paper when there is one
    stem = os.path.splitext(primary.name)[0]
    folders = [nfc(p) for p in reversed(rel_parts)]

    def override(field_name: str):
        for pdf in members:
            if field_name in specs.get(pdf.name, {}):
                return specs[pdf.name][field_name], f"Angabe zur Datei in {sidecar_rel}"
        if field_name in defaults:
            return defaults[field_name][0], f"Vorgabe für den Ordner in {defaults[field_name][1]}"
        return None, None

    value, src = override("subject")
    if value is not None:
        subject, subject_from = value, FROM_SIDECAR
    elif rel_parts:
        subject, subject_from = nfc(rel_parts[0]), FROM_FOLDER
    else:
        subject, subject_from = UNSORTED_SUBJECT, FROM_DEFAULT

    value, src = override("kind")
    if value is not None:
        kind, kind_from = value, FROM_SIDECAR
        kind_note = src
    else:
        kind, kind_from, kind_note = derive_kind(stem, folders)

    value, src = override("semester")
    if value is not None:
        (semester, semester_raw), semester_from = value, FROM_SIDECAR
        semester_note = f"{src}, dort „{semester_raw}“"
    else:
        semester, semester_raw, semester_from, semester_note = derive_semester(stem, folders)

    value, src = override("title")
    title, title_from = (value, FROM_SIDECAR) if value is not None else (readable_title(stem), FROM_FILENAME)

    lecturers, lecturers_src = defaults.get("lecturers", ([], None))
    rec = EntryRec(
        id=entry_id(key), subject=subject, semester=semester, title=title, kind=kind, origin=ORIGIN,
        semester_raw=semester_raw, semester_note=semester_note, kind_note=kind_note,
        subject_from=subject_from, semester_from=semester_from, kind_from=kind_from, title_from=title_from,
        lecturers=list(lecturers), lecturers_source_file=lecturers_src,
    )
    for pdf in members:
        _, role, role_from, note = plan[pdf.name]
        rec.files.append(FileRec(role=role, rel_path=pdf.rel_path, bytes=pdf.size, sha256=pdf.sha256,
                                 mtime_ns=pdf.mtime_ns, sha256_from=FROM_COMPUTED, role_from=role_from, role_note=note))
    return rec
