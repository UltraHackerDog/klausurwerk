"""Indexer: `uv run python -m app.index`

Builds the catalogue tables and extracts per-page text into the FTS5 index.
Two ways to get a catalogue, chosen per run:

  manifest mode  the collection root has a `manifest.json` (plus optional CSV manifests);
  scan mode      no `manifest.json`, or `--scan`: the folder tree is scanned for PDFs
                 and the metadata is derived from names (see app/scan.py).

Re-runnable and incremental: a file whose fingerprint (sha256 + size + mtime +
text source) is unchanged is skipped.

The collection is never written to.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import scan as scanner
from . import semesters
from .config import Config, load_config, root_problem
from .db import connect, init_schema
from .records import FROM_MANIFEST, EntryRec, FileRec

OCR_DIR = "_ocr"
MODE_MANIFEST = "manifest"
MODE_SCAN = "scan"


# --------------------------------------------------------------------------- #
# Reading the manifests
# --------------------------------------------------------------------------- #

def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(fh)]


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _slug(text: str) -> str:
    text = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:80] or "x"


def _canonical_semester(raw: str | None) -> tuple[str, str | None]:
    """Return (semester, note). Unknown stays '????'."""
    raw = (raw or "").strip()
    if semesters.sort_key(raw) is not None:
        return raw, None
    converted = semesters.from_short(raw)
    if converted:
        return converted, f"Schreibweise umgerechnet aus „{raw}“"
    if raw and raw != semesters.UNKNOWN:
        return semesters.UNKNOWN, f"Angabe „{raw}“ nennt kein Semester – nicht zugeordnet"
    return semesters.UNKNOWN, None


def read_manifest_json(root: Path) -> list[EntryRec]:
    path = root / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("manifest.json: expected a list of entries")
    out: list[EntryRec] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        sem, note = _canonical_semester(item.get("year"))
        kind = item.get("kind") if item.get("kind") in ("exam", "exercise") else "material"
        rec = EntryRec(
            id=str(item["id"]),
            subject=str(item.get("subject") or "Unbekannt"),
            semester=sem,
            semester_raw=item.get("year"),
            semester_note=note,
            title=str(item.get("title") or item["id"]),
            kind=kind,
            kind_note=None if kind == item.get("kind") else f"Art „{item.get('kind')}“ im Manifest",
            origin="manifest.json",
            source_page=item.get("source_page"),
            retrieved_at=item.get("retrieved_at"),
        )
        for role in ("angabe", "loesung"):
            rel = item.get(f"{role}_file")
            if rel:
                rec.files.append(FileRec(
                    role=role, rel_path=str(rel),
                    bytes=_int(item.get(f"{role}_bytes")),
                    sha256=item.get(f"{role}_sha256"),
                    url=item.get(f"{role}_url"),
                    retrieved_at=item.get("retrieved_at"),
                ))
        out.append(rec)
    return out


_LABEL_ROLE = re.compile(r"\s*\((Angabe|Lösung)\)\s*$")
_EXAM_WORDS = re.compile(r"(testexam|klausur|semestrale)", re.IGNORECASE)
_LABEL_FERIENKURS = re.compile(r"^Ferienkurs (.+?), (\d{4}[ws]), \w+ \d+$")
_LABEL_LISTING = re.compile(r"^([^,()]+),([^,()]+),([^,()]+)$")


def _match_subject(candidate: str, known: dict[str, str]) -> str | None:
    return known.get(re.sub(r"\s+", "", candidate).casefold())


def read_extra_manifests(root: Path, known_subjects: set[str]) -> list[EntryRec]:
    """manifest_wayback.csv and manifest_github.csv. Only rows with a non-empty
    `file` exist on disk. A solution file is paired with its paper only when the
    manifest itself says so (…testexamsolution… next to …testexam…, or an equal
    label ending in (Angabe)/(Lösung))."""
    known = {re.sub(r"\s+", "", s).casefold(): s for s in known_subjects}
    groups: dict[tuple[str, str], dict] = {}

    for row in _read_csv(root / "manifest_wayback.csv"):
        rel = row.get("file")
        if not rel:
            continue
        typ = row.get("type", "")
        role = "loesung" if "solution" in typ else "angabe"
        key = rel.replace("testexamsolution", "testexam")
        g = groups.setdefault(("manifest_wayback.csv", key), {"rows": [], "files": []})
        g["rows"].append(row)
        g["files"].append(FileRec(
            role=role, rel_path=rel, bytes=_int(row.get("bytes")), sha256=row.get("sha256") or None,
            url=row.get("original_url") or None, archive_url=row.get("archive_url") or None,
            retrieved_at=row.get("retrieved_at") or None,
        ))
        if role == "angabe" or "meta" not in g:
            sem, note = _canonical_semester(row.get("semester"))
            base_type = typ.replace("solution", "")
            kind = "exam" if base_type == "testexam" else "exercise" if base_type == "exercise" else "material"
            stem = Path(key).stem
            g["meta"] = dict(
                subject=row.get("subject") or "Unbekannt", semester=sem, semester_raw=row.get("semester"),
                semester_note=note, kind=kind, kind_note=f"aus Spalte type = „{typ}“",
                title=f"Ferienkurs-Archiv: {stem}", id_hint=f"wb-{_slug(stem)}",
                source_page=row.get("archive_url") or None, retrieved_at=row.get("retrieved_at") or None,
            )

    for row in _read_csv(root / "manifest_github.csv"):
        rel = row.get("file")
        if not rel:
            continue
        label = row.get("label", "")
        name = Path(rel).name
        role, base_label = "angabe", label
        m = _LABEL_ROLE.search(label)
        if m:
            base_label = label[: m.start()]
            role = "loesung" if m.group(1) == "Lösung" else "angabe"
        elif "testexamsolution" in name:
            role = "loesung"
        key = base_label if m else rel.replace("testexamsolution", "testexam")
        g = groups.setdefault(("manifest_github.csv", key), {"rows": [], "files": []})
        g["files"].append(FileRec(
            role=role, rel_path=rel, bytes=_int(row.get("bytes")), sha256=row.get("sha256") or None,
            url=row.get("source_url") or None, retrieved_at=row.get("retrieved_at") or None,
        ))
        if role == "angabe" or "meta" not in g:
            # Only two label shapes are read as structured data, and only when their
            # first part names a subject that already exists. Anything else is a
            # free-text remark: it is kept as a note and nothing is derived from it.
            subject, sem_raw, structured = "GitHub", None, False
            fk = _LABEL_FERIENKURS.match(base_label)
            fs = _LABEL_LISTING.match(base_label)
            if fk and _match_subject(fk.group(1), known):
                subject, sem_raw, structured = _match_subject(fk.group(1), known), fk.group(2), True
            elif fs and _match_subject(fs.group(1), known):
                subject, sem_raw, structured = _match_subject(fs.group(1), known), fs.group(3).strip(), True
            sem, note = _canonical_semester(sem_raw)
            source_set = row.get("source_set") or row.get("repo") or "GitHub"
            if structured:
                kind = "exam" if _EXAM_WORDS.search(base_label) else "exercise" if "exercise" in base_label else "material"
                kind_note = "aus der Beschriftung in manifest_github.csv"
                title = f"{base_label.replace('testexamsolution', 'testexam')} ({source_set})"
                source_note = None
            else:
                kind, kind_note = "material", "Art im Verzeichnis nicht angegeben"
                title = f"{name} ({source_set})"
                source_note = label or None
            stem = Path(rel).stem.replace("testexamsolution", "testexam")
            g["meta"] = dict(
                subject=subject, semester=sem, semester_raw=sem_raw, semester_note=note, kind=kind,
                kind_note=kind_note, title=title, source_note=source_note,
                id_hint=f"gh-{_slug((row.get('source_set') or '') + '-' + (base_label if m else stem))}",
                source_page=f"https://github.com/{row.get('repo')}" if row.get("repo") else None,
                retrieved_at=row.get("retrieved_at") or None,
            )

    out: list[EntryRec] = []
    used: set[str] = set()
    for (origin, _key), g in groups.items():
        meta = g["meta"]
        entry_id = meta.pop("id_hint")
        if entry_id in used:
            entry_id = f"{entry_id}-{(g['files'][0].sha256 or 'x')[:8]}"
        used.add(entry_id)
        roles_seen: set[str] = set()
        files = []
        for f in g["files"]:           # at most one file per role per entry
            if f.role in roles_seen:
                continue
            roles_seen.add(f.role)
            files.append(f)
        out.append(EntryRec(id=entry_id, origin=origin, files=files, **meta))
    return out


# --------------------------------------------------------------------------- #
# Catalogue refresh
# --------------------------------------------------------------------------- #

def read_manifests(root: Path) -> list[EntryRec]:
    entries = read_manifest_json(root)   # raises when missing: fail closed, keep the old index
    subjects = {e.subject for e in entries}
    entries += read_extra_manifests(root, subjects)
    for e in entries:                    # everything here is as the manifests state it
        e.subject_from = e.semester_from = e.kind_from = e.title_from = FROM_MANIFEST
        for f in e.files:
            f.role_from = FROM_MANIFEST
            f.sha256_from = FROM_MANIFEST if f.sha256 else None
    return entries


def previous_fingerprints(con: sqlite3.Connection) -> dict[str, tuple[int | None, int | None, str | None]]:
    """rel_path -> (bytes, mtime_ns, sha256) of files hashed by an earlier scan."""
    rows = con.execute(
        "SELECT rel_path, bytes, mtime_ns, sha256 FROM files WHERE sha256_from = 'computed' LIMIT 1000000"
    ).fetchall()
    return {r["rel_path"]: (r["bytes"], r["mtime_ns"], r["sha256"]) for r in rows}


def refresh_catalogue(con: sqlite3.Connection, root: Path, run_id: str,
                      entries: list[EntryRec] | None = None) -> dict[str, int]:
    if entries is None:
        entries = read_manifests(root)

    readability = {r["file"]: r for r in _read_csv(root / "readability.csv") if r.get("file")}
    seen_paths: set[str] = set()
    duplicate_paths = 0

    for e in entries:
        has_solution = int(any(f.role == "loesung" for f in e.files))
        con.execute(
            """INSERT INTO entries(id, subject, semester, semester_raw, semester_note, sem_sort, title, kind,
                                   kind_note, source_note, origin, source_page, retrieved_at, has_solution, seen_run,
                                   subject_from, semester_from, kind_from, title_from)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 subject_from = excluded.subject_from, semester_from = excluded.semester_from,
                 kind_from = excluded.kind_from, title_from = excluded.title_from,
                 subject = excluded.subject, semester = excluded.semester, semester_raw = excluded.semester_raw,
                 semester_note = excluded.semester_note, sem_sort = excluded.sem_sort, title = excluded.title,
                 kind = excluded.kind, kind_note = excluded.kind_note, source_note = excluded.source_note, origin = excluded.origin,
                 source_page = excluded.source_page, retrieved_at = excluded.retrieved_at,
                 has_solution = excluded.has_solution, seen_run = excluded.seen_run""",
            (e.id, e.subject, e.semester, e.semester_raw, e.semester_note, semesters.sort_key(e.semester),
             e.title, e.kind, e.kind_note, e.source_note, e.origin, e.source_page, e.retrieved_at, has_solution, run_id,
             e.subject_from, e.semester_from, e.kind_from, e.title_from),
        )
        for f in e.files:
            if f.rel_path in seen_paths:
                duplicate_paths += 1
                continue
            seen_paths.add(f.rel_path)
            full = _safe_join(root, f.rel_path)
            on_disk = int(full is not None and full.is_file())
            ocr = _safe_join(root / OCR_DIR, f.rel_path)
            is_pdf = int(f.rel_path.lower().endswith(".pdf"))
            r = readability.get(f.rel_path, {})
            con.execute(
                """INSERT INTO files(entry_id, role, rel_path, is_pdf, bytes, sha256, url, archive_url, retrieved_at,
                                     on_disk, has_ocr_pdf, pages, text_pages, image_only_pages, empty_pages, chars,
                                     clean_ratio, verdict, seen_run, mtime_ns, sha256_from, role_from, role_note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(rel_path) DO UPDATE SET
                     mtime_ns = excluded.mtime_ns, sha256_from = excluded.sha256_from,
                     role_from = excluded.role_from, role_note = excluded.role_note,
                     entry_id = excluded.entry_id, role = excluded.role, is_pdf = excluded.is_pdf,
                     bytes = excluded.bytes, sha256 = excluded.sha256, url = excluded.url,
                     archive_url = excluded.archive_url, retrieved_at = excluded.retrieved_at,
                     on_disk = excluded.on_disk, has_ocr_pdf = excluded.has_ocr_pdf, pages = excluded.pages,
                     text_pages = excluded.text_pages, image_only_pages = excluded.image_only_pages,
                     empty_pages = excluded.empty_pages, chars = excluded.chars,
                     clean_ratio = excluded.clean_ratio, verdict = excluded.verdict, seen_run = excluded.seen_run""",
                (e.id, f.role, f.rel_path, is_pdf, f.bytes, f.sha256, f.url, f.archive_url, f.retrieved_at,
                 on_disk, int(bool(is_pdf and ocr is not None and ocr.is_file())),
                 _int(r.get("pages")), _int(r.get("text_pages")), _int(r.get("image_only_pages")),
                 _int(r.get("empty_pages")), _int(r.get("chars")), _float(r.get("clean_ratio")),
                 r.get("verdict") or None, run_id, f.mtime_ns, f.sha256_from, f.role_from, f.role_note),
            )

    # Drop what the manifests no longer list. Progress rows are kept on purpose.
    removed_files = con.execute("DELETE FROM files WHERE seen_run <> ?", (run_id,)).rowcount
    removed_entries = con.execute("DELETE FROM entries WHERE seen_run <> ?", (run_id,)).rowcount

    # Lecturers named in a sidecar belong to single entries: replace wholesale.
    con.execute("DELETE FROM entry_lecturers")
    for e in entries:
        for name in dict.fromkeys(e.lecturers):
            con.execute("INSERT INTO entry_lecturers(entry_id, name, source, source_file) VALUES (?, ?, 'sidecar', ?)",
                        (e.id, name, e.lecturers_source_file))

    # Lecturer and examiner maps are small and optional: replace wholesale.
    con.execute("DELETE FROM lecturer_names")
    con.execute("DELETE FROM lecturer_rows")
    for row in _read_csv(root / "lecturer_map.csv"):
        if not row.get("module") or not row.get("semester"):
            continue
        cur = con.execute(
            "INSERT INTO lecturer_rows(module, semester, lecturers, source, caveat, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (row["module"], row["semester"], row.get("lecturers", ""), row.get("source") or None,
             row.get("caveat") or None, row.get("recorded_at") or None),
        )
        for name in (n.strip() for n in row.get("lecturers", "").split(";")):
            if name:
                con.execute(
                    "INSERT INTO lecturer_names(row_id, module, semester, name) VALUES (?, ?, ?, ?)",
                    (cur.lastrowid, row["module"], row["semester"], name),
                )
    con.execute("DELETE FROM examiners")
    for row in _read_csv(root / "professor_map.csv"):
        if not row.get("professor") or not row.get("course"):
            continue
        con.execute(
            """INSERT INTO examiners(professor, course, exam_type, year_as_listed, semester, listed_title, source, retrieved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["professor"], row["course"], row.get("exam_type") or None, row.get("year_as_listed") or None,
             row.get("semester") or None, row.get("listed_title") or None, row.get("source") or None,
             row.get("retrieved_at") or None),
        )
    con.commit()
    return {"removed_entries": removed_entries, "removed_files": removed_files, "duplicate_paths": duplicate_paths}


def _safe_join(base: Path, rel: str) -> Path | None:
    """Join and verify the result stays inside `base`. None when it does not."""
    try:
        base_r = base.resolve()
        full = (base_r / rel).resolve()
        full.relative_to(base_r)
        return full
    except (ValueError, OSError, RuntimeError):
        return None


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #

_WS = re.compile(r"[ \t ]+")


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    lines = [_WS.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def extract_pages(original: Path, ocr_pdf: Path | None, ocr_txt: Path | None) -> tuple[list[str], str]:
    """Per-page text and the name of the text source actually used."""
    import pymupdf as fitz

    fitz.TOOLS.mupdf_display_errors(False)
    source_path, source = (ocr_pdf, "ocr_pdf") if ocr_pdf else (original, "original")
    with fitz.open(str(source_path)) as doc:
        if doc.needs_pass:
            raise ValueError("encrypted")
        texts = [_clean(page.get_text("text")) for page in doc]

    if ocr_txt is not None and any(not t for t in texts):
        parts = ocr_txt.read_text(encoding="utf-8", errors="replace").split("\f")
        if parts and not parts[-1].strip():
            parts = parts[:-1]
        if len(parts) == len(texts):          # only trust the sidecar when pages line up
            filled = False
            for i, t in enumerate(texts):
                if not t and _clean(parts[i]):
                    texts[i] = _clean(parts[i])
                    filled = True
            if filled:
                source = f"{source}+ocr_txt"
    return texts, source


def index_text(con: sqlite3.Connection, root: Path, verbose: bool = True) -> dict[str, int]:
    rows = con.execute(
        "SELECT id, rel_path, sha256, idx_fingerprint, idx_error FROM files "
        "WHERE is_pdf = 1 AND on_disk = 1 ORDER BY id LIMIT 100000"
    ).fetchall()
    stats = {"examined": 0, "extracted": 0, "skipped_unchanged": 0, "failed": 0}
    started = time.monotonic()
    for n, row in enumerate(rows, 1):
        stats["examined"] += 1
        original = _safe_join(root, row["rel_path"])
        if original is None or not original.is_file():
            continue
        ocr_pdf = _safe_join(root / OCR_DIR, row["rel_path"])
        if ocr_pdf is not None and not ocr_pdf.is_file():
            ocr_pdf = None
        ocr_txt = _safe_join(root / OCR_DIR, str(Path(row["rel_path"]).with_suffix(".txt")))
        if ocr_txt is not None and not ocr_txt.is_file():
            ocr_txt = None

        st = original.stat()
        parts = [row["sha256"] or "", str(st.st_size), str(st.st_mtime_ns)]
        for extra in (ocr_pdf, ocr_txt):
            parts.append(str(extra.stat().st_mtime_ns) if extra else "-")
        fingerprint = ":".join(parts)
        if row["idx_fingerprint"] == fingerprint and row["idx_error"] is None:
            stats["skipped_unchanged"] += 1
            continue

        con.execute("DELETE FROM pages WHERE file_id = ?", (row["id"],))
        try:
            texts, source = extract_pages(original, ocr_pdf, ocr_txt)
        except Exception as exc:  # a broken PDF must not stop the run
            stats["failed"] += 1
            con.execute(
                "UPDATE files SET idx_fingerprint = ?, idx_text_source = NULL, idx_pages = NULL, "
                "idx_pages_with_text = 0, idx_error = ? WHERE id = ?",
                (fingerprint, f"{type(exc).__name__}: {exc}"[:300], row["id"]),
            )
            continue
        with_text = 0
        for page_no, text in enumerate(texts, 1):
            if text:
                with_text += 1
                con.execute("INSERT INTO pages(file_id, page_no, text) VALUES (?, ?, ?)", (row["id"], page_no, text))
        con.execute(
            "UPDATE files SET idx_fingerprint = ?, idx_text_source = ?, idx_pages = ?, idx_pages_with_text = ?, "
            "idx_error = NULL WHERE id = ?",
            (fingerprint, source, len(texts), with_text, row["id"]),
        )
        stats["extracted"] += 1
        if n % 50 == 0:
            con.commit()
        if verbose and n % 250 == 0:
            print(f"  … {n}/{len(rows)} Dateien ({time.monotonic() - started:.0f} s)", flush=True)
    con.commit()
    return stats


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def measured_counts(con: sqlite3.Connection) -> dict[str, int]:
    def one(sql: str) -> int:
        return int(con.execute(sql).fetchone()[0] or 0)

    return {
        "entries": one("SELECT COUNT(*) FROM entries"),
        "entries_exam": one("SELECT COUNT(*) FROM entries WHERE kind = 'exam'"),
        "entries_exercise": one("SELECT COUNT(*) FROM entries WHERE kind = 'exercise'"),
        "entries_material": one("SELECT COUNT(*) FROM entries WHERE kind = 'material'"),
        "entries_semester_unknown": one("SELECT COUNT(*) FROM entries WHERE sem_sort IS NULL"),
        "subjects": one("SELECT COUNT(DISTINCT subject) FROM entries"),
        "files": one("SELECT COUNT(*) FROM files"),
        "files_pdf": one("SELECT COUNT(*) FROM files WHERE is_pdf = 1"),
        "files_not_pdf": one("SELECT COUNT(*) FROM files WHERE is_pdf = 0"),
        "files_missing_on_disk": one("SELECT COUNT(*) FROM files WHERE on_disk = 0"),
        "files_with_ocr_copy": one("SELECT COUNT(*) FROM files WHERE has_ocr_pdf = 1"),
        "files_text_from_ocr": one("SELECT COUNT(*) FROM files WHERE idx_text_source LIKE 'ocr%'"),
        "files_without_readability_row": one("SELECT COUNT(*) FROM files WHERE is_pdf = 1 AND verdict IS NULL"),
        "pdf_pages_seen": one("SELECT COALESCE(SUM(idx_pages), 0) FROM files"),
        "pages_indexed": one("SELECT COUNT(*) FROM pages"),
        "files_without_text": one("SELECT COUNT(*) FROM files WHERE is_pdf = 1 AND on_disk = 1 "
                                  "AND idx_error IS NULL AND COALESCE(idx_pages_with_text, 0) = 0"),
        "files_failed_to_open": one("SELECT COUNT(*) FROM files WHERE idx_error IS NOT NULL"),
        "lecturer_rows": one("SELECT COUNT(*) FROM lecturer_rows"),
        "sidecar_lecturer_rows": one("SELECT COUNT(*) FROM entry_lecturers"),
        "examiner_rows": one("SELECT COUNT(*) FROM examiners"),
    }


def _set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def previous_mode(con: sqlite3.Connection) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = 'index_mode'").fetchone()
    if row:
        return row["value"]
    # Databases written before the mode was recorded were always built from manifests.
    if con.execute("SELECT 1 FROM entries WHERE origin = 'manifest.json' LIMIT 1").fetchone():
        return MODE_MANIFEST
    return None


def run(cfg: Config, verbose: bool = True, with_text: bool = True, scan: bool = False) -> dict:
    root = cfg.collection_root
    problem = root_problem(cfg)
    if problem:
        raise SystemExit(f"{problem} — index left untouched")
    has_manifest = (root / "manifest.json").is_file()
    mode = MODE_SCAN if scan or not has_manifest else MODE_MANIFEST
    con = connect(cfg.db_path)
    try:
        init_schema(con)
        # An index built from manifests is not silently replaced by a folder scan
        # (a missing manifest.json is more likely an unmounted or half-copied folder).
        if mode == MODE_SCAN and not scan and previous_mode(con) == MODE_MANIFEST:
            raise SystemExit(f"manifest.json not found under {root} — index left untouched "
                             f"(it was built from manifests; pass --scan to rebuild it from a folder scan)")
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Unique per run: two runs inside the same second must not look like one.
        run_id = f"{started}#{uuid.uuid4().hex[:8]}"
        if verbose:
            print(f"Sammlung: {root}\nDatenbank: {cfg.db_path}\nModus: {mode}")
        report: dict = {}
        if mode == MODE_SCAN:
            entries, report = scanner.scan_collection(root, cfg.scan_max_files, previous_fingerprints(con))
            if not entries:
                raise SystemExit(f"no PDF found under {root} — index left untouched")
            cat = refresh_catalogue(con, root, run_id, entries)
        else:
            cat = refresh_catalogue(con, root, run_id)
        text = index_text(con, root, verbose) if with_text else {}
        _set_meta(con, "indexed_at", started)
        _set_meta(con, "index_mode", mode)
        _set_meta(con, "scan_report", json.dumps(report, ensure_ascii=False))
        con.commit()
        counts = measured_counts(con)
    finally:
        con.close()
    result = {"run": started, "mode": mode, "catalogue": cat, "scan": report, "text": text, "counts": counts}
    if verbose:
        print("\nDieser Lauf:")
        for k, v in {**cat, **text}.items():
            print(f"  {k:32} {v}")
        if mode == MODE_SCAN:
            print("Ordner-Scan:")
            for k, v in report.items():
                if k == "sidecars_rejected":
                    print(f"  {k:32} {len(v)}")
                    for bad in v:
                        print(f"    ! {bad['file']}: {bad['error']}")
                elif k == "other_extensions":
                    top = sorted(v.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
                    print(f"  {k:32} {', '.join(f'{ext} {n}' for ext, n in top) or '-'}")
                else:
                    print(f"  {k:32} {v}")
            if report["cap_hit"]:
                print(f"  ! Scan bei {report['max_files']} Dateien abgebrochen (scan_max_files in config.json). "
                      f"Der Katalog ist unvollständig.")
        print("Gemessener Stand des Index:")
        for k, v in counts.items():
            print(f"  {k:32} {v}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or refresh the Klausurwerk index.")
    parser.add_argument("--no-text", action="store_true", help="refresh the catalogue only, skip text extraction")
    parser.add_argument("--scan", action="store_true",
                        help="scan the folder tree for PDFs even when a manifest.json exists")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    run(load_config(), verbose=not args.quiet, with_text=not args.no_text, scan=args.scan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
