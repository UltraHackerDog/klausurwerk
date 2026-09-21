"""Klausurwerk — local revision platform. Loopback only, no external requests.

Rules kept throughout this module:
  * every SQL statement is a static string with bound parameters;
    the only dynamic SQL part is ORDER BY, chosen from an allow-list;
  * every list query carries LIMIT/OFFSET with a clamped maximum;
  * the file endpoint serves only paths present in the index and fails closed.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import markdown, semesters
from .config import Config, load_config, root_problem
from .db import connect, init_schema

STATIC_DIR = Path(__file__).resolve().parent / "static"
OCR_DIR = "_ocr"
MAX_LIMIT = 200
STATUSES = ("offen", "in_arbeit", "erledigt")

LABEL_SAME_LECTURER = "gleicher Dozent (laut Vorlesungsverzeichnis)"
LABEL_NAMED_EXAMINER = "Prüfer laut Fachschaft"
LABEL_SIDECAR_LECTURER = "gleicher Dozent (laut klausurwerk.json)"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Refuse to start against a folder that is not there, and say what to change.
    problem = root_problem(load_config())
    if problem:
        raise RuntimeError(problem)
    yield


app = FastAPI(title="Klausurwerk", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
# Refuse requests addressed to any other host name (DNS-rebinding guard).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if not request.url.path.startswith("/files/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-src 'self'; object-src 'self'; "
            "base-uri 'none'; form-action 'self'; connect-src 'self'; img-src 'self' data:"
        )
    return response


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_SCHEMA_READY: set[str] = set()


def get_config() -> Config:
    return load_config()


def get_db(cfg: Config = Depends(get_config)) -> Iterator[sqlite3.Connection]:
    # One connection per request: sqlite connections are not shared across threads.
    con = connect(cfg.db_path)
    try:
        key = str(cfg.db_path)
        if key not in _SCHEMA_READY:      # once per database file, not once per request
            init_schema(con)
            _SCHEMA_READY.add(key)
        yield con
    finally:
        con.close()


def clamp_limit(limit: int | None, default: int = 50, maximum: int = MAX_LIMIT) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), maximum))


def clamp_offset(offset: int | None) -> int:
    return max(0, min(int(offset or 0), 1_000_000))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _surname_initial(name: str) -> tuple[str, str | None]:
    """('beispiel', 'a') from 'Prof. Dr. A. Beispiel' or 'Anna Beispiel'."""
    tokens = [t for t in re.split(r"\s+", name.strip()) if t and t.rstrip(".").casefold() not in ("prof", "dr")]
    if not tokens:
        return "", None
    surname = tokens[-1].casefold()
    initial = tokens[0][0].casefold() if len(tokens) > 1 else None
    return surname, initial


def matching_professors(con: sqlite3.Connection, lecturers: tuple[str, ...] | list[str]) -> list[str]:
    """professor_map spellings ('Prof. Dr. A. Beispiel') that match configured names
    ('Anna Beispiel') by surname and first initial. The match rule is shown in the UI."""
    wanted = [_surname_initial(n) for n in lecturers]
    rows = con.execute("SELECT DISTINCT professor FROM examiners ORDER BY professor LIMIT 1000").fetchall()
    out = []
    for r in rows:
        s, i = _surname_initial(r["professor"])
        for ws, wi in wanted:
            if s and s == ws and (i is None or wi is None or i == wi):
                out.append(r["professor"])
                break
    return out


def _entry_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["semester_label"] = semesters.display(d.get("semester"))
    d["semester_known"] = semesters.sort_key(d.get("semester")) is not None
    return d


# --------------------------------------------------------------------------- #
# Meta, subjects, semesters, lecturers
# --------------------------------------------------------------------------- #

@app.get("/api/meta")
def api_meta(con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    def one(sql: str) -> int:
        return int(con.execute(sql).fetchone()[0] or 0)

    meta = {r["key"]: r["value"] for r in con.execute(
        "SELECT key, value FROM meta WHERE key IN ('indexed_at', 'index_mode', 'scan_report') LIMIT 3")}
    try:
        scan = json.loads(meta.get("scan_report") or "{}") or None
    except ValueError:
        scan = None
    labels = {"same_lecturer": LABEL_SAME_LECTURER, "named_examiner": LABEL_NAMED_EXAMINER}
    if one("SELECT EXISTS (SELECT 1 FROM entry_lecturers)"):
        labels["sidecar_lecturer"] = LABEL_SIDECAR_LECTURER
    return {
        "app": "Klausurwerk",
        "user_id": cfg.user_id,
        "indexed_at": meta.get("indexed_at"),
        "index_mode": meta.get("index_mode"),     # manifest | scan | None (not indexed yet)
        "scan": scan,                             # the last folder scan's own summary
        "counts": {
            "entries": one("SELECT COUNT(*) FROM entries"),
            "files": one("SELECT COUNT(*) FROM files"),
            "pages_indexed": one("SELECT COUNT(*) FROM pages"),
            "subjects": one("SELECT COUNT(DISTINCT subject) FROM entries"),
            "semester_unknown": one("SELECT COUNT(*) FROM entries WHERE sem_sort IS NULL"),
        },
        "unknown_semester_label": semesters.UNKNOWN_LABEL,
        "labels": labels,
    }


@app.get("/api/subjects")
def api_subjects(limit: int | None = None, offset: int | None = None,
                 con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    lim, off = clamp_limit(limit, 100), clamp_offset(offset)
    rows = con.execute(
        """SELECT e.subject,
                  COUNT(*) AS total,
                  SUM(e.kind = 'exam') AS exams,
                  SUM(e.kind = 'exercise') AS exercises,
                  SUM(e.kind = 'material') AS material,
                  SUM(COALESCE(p.status, 'offen') = 'erledigt') AS done
           FROM entries e
           LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid
           GROUP BY e.subject ORDER BY e.subject LIMIT :lim OFFSET :off""",
        {"uid": cfg.user_id, "lim": lim, "off": off},
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": lim, "offset": off}


@app.get("/api/semesters")
def api_semesters(subject: str | None = None, limit: int | None = None, offset: int | None = None,
                  con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    lim, off = clamp_limit(limit, 200), clamp_offset(offset)
    rows = con.execute(
        """SELECT e.semester, e.sem_sort, COUNT(*) AS total,
                  SUM(COALESCE(p.status, 'offen') = 'erledigt') AS done
           FROM entries e
           LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid
           WHERE (:subject IS NULL OR e.subject = :subject)
           GROUP BY e.semester, e.sem_sort
           ORDER BY e.sem_sort IS NULL, e.sem_sort DESC LIMIT :lim OFFSET :off""",
        {"uid": cfg.user_id, "subject": subject or None, "lim": lim, "off": off},
    ).fetchall()
    return {"items": [_entry_row(r) for r in rows], "limit": lim, "offset": off}


@app.get("/api/lecturers")
def api_lecturers(limit: int | None = None, offset: int | None = None,
                  con: sqlite3.Connection = Depends(get_db)):
    lim, off = clamp_limit(limit, 200), clamp_offset(offset)
    rows = con.execute(
        """SELECT name, source_kind FROM (
               SELECT DISTINCT name, 'lecturer_map' AS source_kind FROM lecturer_names
               UNION
               SELECT DISTINCT professor AS name, 'professor_map' AS source_kind FROM examiners
               UNION
               SELECT DISTINCT name, 'sidecar' AS source_kind FROM entry_lecturers)
           ORDER BY name LIMIT :lim OFFSET :off""",
        {"lim": lim, "off": off},
    ).fetchall()
    return {"items": [dict(r) for r in rows], "limit": lim, "offset": off}


# --------------------------------------------------------------------------- #
# Entries
# --------------------------------------------------------------------------- #

# ORDER BY cannot be a bound parameter; it is picked from this allow-list only.
ENTRY_ORDER = {
    "semester_desc": " ORDER BY e.sem_sort IS NULL, e.sem_sort DESC, e.subject, e.title, e.id",
    "semester_asc": " ORDER BY e.sem_sort IS NULL, e.sem_sort ASC, e.subject, e.title, e.id",
    "title": " ORDER BY e.title COLLATE NOCASE, e.id",
    "subject": " ORDER BY e.subject, e.sem_sort IS NULL, e.sem_sort DESC, e.title, e.id",
    "status": " ORDER BY COALESCE(p.status, 'offen'), e.sem_sort IS NULL, e.sem_sort DESC, e.id",
}

_ENTRY_SELECT = """
SELECT e.id, e.subject, e.semester, e.title, e.kind, e.origin, e.has_solution,
       COALESCE(p.status, 'offen') AS status, p.score, p.difficulty, p.done_on,
       (SELECT f.verdict FROM files f WHERE f.entry_id = e.id ORDER BY f.role LIMIT 1) AS verdict,
       COALESCE((SELECT group_concat(ln.name, '; ') FROM lecturer_names ln
                  WHERE ln.module = e.subject AND ln.semester = e.semester),
                (SELECT group_concat(el.name, '; ') FROM entry_lecturers el
                  WHERE el.entry_id = e.id)) AS lecturers,
       CASE WHEN EXISTS (SELECT 1 FROM lecturer_names ln WHERE ln.module = e.subject AND ln.semester = e.semester)
              THEN 'lecturer_map'
            WHEN EXISTS (SELECT 1 FROM entry_lecturers el WHERE el.entry_id = e.id) THEN 'sidecar' END AS lecturers_source
FROM entries e
LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid
"""

_ENTRY_WHERE = """
WHERE (:subject IS NULL OR e.subject = :subject)
  AND (:semester IS NULL OR e.semester = :semester)
  AND (:kind IS NULL OR e.kind = :kind)
  AND (:has_solution IS NULL OR e.has_solution = :has_solution)
  AND (:status IS NULL OR COALESCE(p.status, 'offen') = :status)
  AND (:sem_from IS NULL OR e.sem_sort >= :sem_from)
  AND (:sem_to IS NULL OR e.sem_sort <= :sem_to)
  AND (:title_like IS NULL OR e.title LIKE :title_like ESCAPE '\\')
  AND (:verdict IS NULL OR EXISTS (
        SELECT 1 FROM files f WHERE f.entry_id = e.id AND f.verdict = :verdict))
  AND (:dozent IS NULL
       OR EXISTS (SELECT 1 FROM lecturer_names ln
                   WHERE ln.module = e.subject AND ln.semester = e.semester AND ln.name = :dozent)
       OR EXISTS (SELECT 1 FROM entry_lecturers el WHERE el.entry_id = e.id AND el.name = :dozent)
       OR EXISTS (SELECT 1 FROM examiners x
                   WHERE x.course = e.subject AND x.semester = e.semester AND x.professor = :dozent))
"""


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@app.get("/api/entries")
def api_entries(
    subject: str | None = None, semester: str | None = None, kind: str | None = None,
    has_solution: bool | None = None, dozent: str | None = None,
    sem_from: str | None = None, sem_to: str | None = None,
    verdict: str | None = None, status: str | None = None, q: str | None = None,
    sort: str = "semester_desc", limit: int | None = None, offset: int | None = None,
    con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config),
):
    lim, off = clamp_limit(limit), clamp_offset(offset)
    order = ENTRY_ORDER.get(sort)
    if order is None:
        raise HTTPException(400, "unknown sort")
    if kind and kind not in ("exam", "exercise", "material"):
        raise HTTPException(400, "unknown kind")
    if status and status not in STATUSES:
        raise HTTPException(400, "unknown status")
    if verdict and verdict not in ("text", "mixed", "scan", "no_text", "broken"):
        raise HTTPException(400, "unknown verdict")
    params = {
        "uid": cfg.user_id, "subject": subject or None, "semester": semester or None, "kind": kind or None,
        "has_solution": None if has_solution is None else int(has_solution),
        "status": status or None, "verdict": verdict or None, "dozent": dozent or None,
        "sem_from": semesters.sort_key(sem_from), "sem_to": semesters.sort_key(sem_to),
        "title_like": _like(q.strip()[:100]) if q and q.strip() else None,
    }
    total = con.execute(
        "SELECT COUNT(*) FROM entries e LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid"
        + _ENTRY_WHERE, params).fetchone()[0]
    rows = con.execute(_ENTRY_SELECT + _ENTRY_WHERE + order + " LIMIT :lim OFFSET :off",
                       {**params, "lim": lim, "off": off}).fetchall()
    return {"items": [_entry_row(r) for r in rows], "total": total, "limit": lim, "offset": off}


def _active_mock(con: sqlite3.Connection, uid: str, entry_id: str | None = None) -> sqlite3.Row | None:
    return con.execute(
        """SELECT id, entry_id, planned_minutes, started_at FROM mock_sessions
           WHERE user_id = :uid AND finished_at IS NULL AND (:entry IS NULL OR entry_id = :entry)
           ORDER BY id DESC LIMIT 1""",
        {"uid": uid, "entry": entry_id},
    ).fetchone()


@app.get("/api/entries/{entry_id}")
def api_entry(entry_id: str, con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    e = con.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if e is None:
        raise HTTPException(404, "not found")
    entry = _entry_row(e)
    entry.pop("seen_run", None)
    files = [dict(r) for r in con.execute(
        """SELECT id, role, rel_path, is_pdf, bytes, sha256, url, archive_url, retrieved_at, on_disk, has_ocr_pdf,
                  pages, text_pages, image_only_pages, empty_pages, chars, clean_ratio, verdict,
                  idx_text_source, idx_pages, idx_pages_with_text, idx_error, sha256_from, role_from, role_note
           FROM files WHERE entry_id = ? ORDER BY role LIMIT 10""", (entry_id,))]
    lecturer_rows = [dict(r) for r in con.execute(
        """SELECT lecturers, source, caveat, recorded_at, 'lecturer_map.csv' AS source_file FROM lecturer_rows
           WHERE module = ? AND semester = ? ORDER BY id LIMIT 10""", (e["subject"], e["semester"]))]
    # Lecturers a sidecar names for this very entry; they do not depend on a known semester.
    lecturer_rows += [dict(r) for r in con.execute(
        """SELECT group_concat(name, '; ') AS lecturers, source, NULL AS caveat, NULL AS recorded_at, source_file
           FROM entry_lecturers WHERE entry_id = ? GROUP BY source, source_file ORDER BY source_file LIMIT 10""",
        (entry_id,))]
    examiner_rows = [dict(r) for r in con.execute(
        """SELECT professor, exam_type, year_as_listed, listed_title, source, retrieved_at FROM examiners
           WHERE course = ? AND semester = ? ORDER BY id LIMIT 30""", (e["subject"], e["semester"]))]
    p = con.execute("SELECT status, score, difficulty, notes, done_on, updated_at FROM progress "
                    "WHERE user_id = ? AND entry_id = ?", (cfg.user_id, entry_id)).fetchone()
    progress = dict(p) if p else {"status": "offen", "score": None, "difficulty": None, "notes": "",
                                  "done_on": None, "updated_at": None}
    active = _active_mock(con, cfg.user_id, entry_id)
    # Where each catalogue value came from: manifest | folder | filename | sidecar | default.
    # None means the value could not be derived and is shown as unknown.
    provenance = {
        "subject": {"from": entry.get("subject_from")},
        "title": {"from": entry.get("title_from")},
        "kind": {"from": entry.get("kind_from"), "note": entry.get("kind_note")},
        "semester": {"from": entry.get("semester_from"), "note": entry.get("semester_note"),
                     "as_written": entry.get("semester_raw")},
    }
    return {
        "entry": entry, "files": files, "provenance": provenance,
        "lecturers": {"rows": lecturer_rows, "source_file": "lecturer_map.csv",
                      "known": bool(lecturer_rows)},
        "examiners": {"rows": examiner_rows, "source_file": "professor_map.csv",
                      "known": bool(examiner_rows),
                      "note": "Die Fachschaft nennt Prüfer je gelisteter Klausur; die Zuordnung hier erfolgt "
                              "nur über Fach und Semester."},
        "progress": progress,
        "solution_locked": active is not None,
    }


class ProgressIn(BaseModel):
    status: str = Field(pattern="^(offen|in_arbeit|erledigt)$")
    score: int | None = Field(default=None, ge=0, le=100)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    notes: str = Field(default="", max_length=20000)
    done_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@app.put("/api/entries/{entry_id}/progress")
def api_progress(entry_id: str, body: ProgressIn,
                 con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    if con.execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,)).fetchone() is None:
        raise HTTPException(404, "not found")
    if body.done_on:
        try:
            date.fromisoformat(body.done_on)
        except ValueError:
            raise HTTPException(422, "invalid date")
    done_on = body.done_on
    if body.status == "erledigt" and not done_on:
        done_on = date.today().isoformat()
    con.execute(
        """INSERT INTO progress(user_id, entry_id, status, score, difficulty, notes, done_on, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, entry_id) DO UPDATE SET
             status = excluded.status, score = excluded.score, difficulty = excluded.difficulty,
             notes = excluded.notes, done_on = excluded.done_on, updated_at = excluded.updated_at""",
        (cfg.user_id, entry_id, body.status, body.score, body.difficulty, body.notes, done_on, _now()),
    )
    con.commit()
    return {"ok": True, "done_on": done_on}


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #

@app.get("/api/dashboard")
def api_dashboard(limit: int | None = None, offset: int | None = None,
                  con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    lim, off = clamp_limit(limit, 12, 100), clamp_offset(offset)
    modules = []
    for m in cfg.my_modules[:20]:
        profs = matching_professors(con, m.lecturers)
        params = {"uid": cfg.user_id, "subject": m.subject,
                  "names": json.dumps(list(m.lecturers)), "profs": json.dumps(profs)}
        ranked_cte = """
            WITH ranked AS (
              SELECT e.id, e.title, e.semester, e.sem_sort, e.kind, e.has_solution,
                     COALESCE(p.status, 'offen') AS status, p.score,
                     EXISTS (SELECT 1 FROM lecturer_names ln
                              WHERE ln.module = e.subject AND ln.semester = e.semester
                                AND ln.name COLLATE NOCASE IN (SELECT value FROM json_each(:names))) AS same_lecturer,
                     EXISTS (SELECT 1 FROM examiners x
                              WHERE x.course = e.subject AND x.semester = e.semester
                                AND x.professor IN (SELECT value FROM json_each(:profs))) AS named_examiner,
                     EXISTS (SELECT 1 FROM entry_lecturers el
                              WHERE el.entry_id = e.id
                                AND el.name COLLATE NOCASE IN (SELECT value FROM json_each(:names))) AS sidecar_lecturer
              FROM entries e
              LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid
              WHERE e.subject = :subject)
        """
        totals = con.execute(
            ranked_cte + """SELECT COUNT(*) AS total,
                                   COALESCE(SUM(status = 'erledigt'), 0) AS done,
                                   COALESCE(SUM(status = 'in_arbeit'), 0) AS in_progress,
                                   COALESCE(SUM(same_lecturer OR named_examiner OR sidecar_lecturer), 0) AS priority_total,
                                   COALESCE(SUM((same_lecturer OR named_examiner OR sidecar_lecturer)
                                                AND status = 'erledigt'), 0) AS priority_done
                            FROM ranked""", params).fetchone()
        rows = con.execute(
            ranked_cte + """SELECT * FROM ranked
                            ORDER BY (same_lecturer OR named_examiner OR sidecar_lecturer) DESC, (kind = 'exam') DESC,
                                     (kind = 'exercise') DESC, sem_sort IS NULL, sem_sort DESC, title, id
                            LIMIT :lim OFFSET :off""", {**params, "lim": lim, "off": off}).fetchall()
        papers = []
        for r in rows:
            d = _entry_row(r)
            d["reasons"] = ([LABEL_SAME_LECTURER] if d.pop("same_lecturer") else []) + \
                           ([LABEL_NAMED_EXAMINER] if d.pop("named_examiner") else []) + \
                           ([LABEL_SIDECAR_LECTURER] if d.pop("sidecar_lecturer") else [])
            papers.append(d)
        taught = con.execute(
            """SELECT ln.semester, group_concat(DISTINCT ln.name) AS names,
                      (SELECT COUNT(*) FROM entries e WHERE e.subject = ln.module AND e.semester = ln.semester) AS entries
               FROM lecturer_names ln
               WHERE ln.module = :subject AND ln.name COLLATE NOCASE IN (SELECT value FROM json_each(:names))
               GROUP BY ln.semester ORDER BY ln.semester DESC LIMIT 40""", params).fetchall()
        taught_rows = sorted((dict(t) for t in taught),
                             key=lambda t: semesters.sort_key(t["semester"]) or 0, reverse=True)
        for t in taught_rows:
            t["semester_label"] = semesters.display(t["semester"])
            t["is_current"] = t["semester"] == cfg.current_semester
        modules.append({
            "name": m.name, "subject": m.subject, "lecturers": list(m.lecturers),
            "lecturers_source": cfg.my_modules_source or "config.json",
            "matched_professor_spellings": profs,
            "totals": dict(totals), "papers": papers, "taught_semesters": taught_rows,
        })
    # Without configured modules the dashboard is the subject overview with progress.
    subjects: list[dict] = []
    if not modules:
        sub_lim = clamp_limit(limit, 100)
        subjects = [dict(r) for r in con.execute(
            """SELECT e.subject, COUNT(*) AS total,
                      SUM(e.kind = 'exam') AS exams, SUM(e.kind = 'exercise') AS exercises,
                      SUM(e.kind = 'material') AS material,
                      SUM(COALESCE(p.status, 'offen') = 'erledigt') AS done,
                      SUM(COALESCE(p.status, 'offen') = 'in_arbeit') AS in_progress
               FROM entries e
               LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid
               GROUP BY e.subject ORDER BY e.subject LIMIT :lim OFFSET :off""",
            {"uid": cfg.user_id, "lim": sub_lim, "off": off})]
    subjects_total = con.execute("SELECT COUNT(DISTINCT subject) FROM entries").fetchone()[0]
    return {"modules": modules, "subjects": subjects, "subjects_total": subjects_total,
            "subjects_limit": clamp_limit(limit, 100), "limit": lim, "offset": off,
            "match_rule": "Prüfer-Namen der Fachschaft werden über Nachname und ersten Buchstaben des Vornamens "
                          "mit den Dozenten aus config.json abgeglichen.",
            "caveat": "Das Vorlesungsverzeichnis nennt, wer gelesen hat – nicht, wer die Klausur gestellt hat."}


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

_TOKEN = re.compile(r"\w+", re.UNICODE)


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query: every word quoted, AND-joined,
    the last word as a prefix. FTS operators in user input are neutralised."""
    tokens = _TOKEN.findall(text or "")[:12]
    if not tokens:
        return None
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    quoted[-1] += "*"
    return " ".join(quoted)


@app.get("/api/search")
def api_search(q: str = "", subject: str | None = None, include_solutions: bool = True,
               limit: int | None = None, offset: int | None = None,
               con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    lim, off = clamp_limit(limit, 20, 100), clamp_offset(offset)
    match = fts_query(q[:200])
    if match is None:
        return {"items": [], "total": 0, "limit": lim, "offset": off, "query": q}
    params = {"q": match, "subject": subject or None, "sol": int(include_solutions), "uid": cfg.user_id}
    where = """
        FROM pages_fts
        JOIN pages pg ON pg.id = pages_fts.rowid
        JOIN files f ON f.id = pg.file_id
        JOIN entries e ON e.id = f.entry_id
        WHERE pages_fts MATCH :q
          AND (:subject IS NULL OR e.subject = :subject)
          AND (:sol = 1 OR f.role = 'angabe')
          AND NOT (f.role = 'loesung' AND EXISTS (
                SELECT 1 FROM mock_sessions ms
                 WHERE ms.user_id = :uid AND ms.entry_id = e.id AND ms.finished_at IS NULL))
    """
    try:
        total = con.execute("SELECT COUNT(*) " + where, params).fetchone()[0]
        rows = con.execute(
            """SELECT pg.page_no, f.rel_path, f.role, f.has_ocr_pdf, f.idx_text_source,
                      e.id AS entry_id, e.title, e.subject, e.semester, e.kind,
                      snippet(pages_fts, 0, char(1), char(2), ' … ', 16) AS snippet """
            + where + " ORDER BY rank LIMIT :lim OFFSET :off", {**params, "lim": lim, "off": off}).fetchall()
    except sqlite3.OperationalError:
        raise HTTPException(400, "Suchanfrage nicht lesbar")
    items = []
    for r in rows:
        d = _entry_row(r)
        d["snippet"] = re.sub(r"\s+", " ", d["snippet"] or "")[:320]
        items.append(d)
    return {"items": items, "total": total, "limit": lim, "offset": off, "query": q}


# --------------------------------------------------------------------------- #
# Mock exams
# --------------------------------------------------------------------------- #

class MockStart(BaseModel):
    entry_id: str = Field(min_length=1, max_length=200)
    minutes: int = Field(default=90, ge=5, le=360)


class MockFinish(BaseModel):
    score: int | None = Field(default=None, ge=0, le=100)


def _mock_row(r: sqlite3.Row) -> dict:
    return _entry_row(r)


@app.post("/api/mock/start")
def api_mock_start(body: MockStart, con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    if con.execute("SELECT 1 FROM entries WHERE id = ?", (body.entry_id,)).fetchone() is None:
        raise HTTPException(404, "not found")
    if _active_mock(con, cfg.user_id) is not None:
        raise HTTPException(409, "Es läuft bereits eine Probeklausur.")
    cur = con.execute(
        "INSERT INTO mock_sessions(user_id, entry_id, planned_minutes, started_at) VALUES (?, ?, ?, ?)",
        (cfg.user_id, body.entry_id, body.minutes, _now()))
    con.commit()
    return {"id": cur.lastrowid}


@app.get("/api/mock/active")
def api_mock_active(con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    r = con.execute(
        """SELECT ms.id, ms.entry_id, ms.planned_minutes, ms.started_at, e.title, e.subject, e.semester
           FROM mock_sessions ms LEFT JOIN entries e ON e.id = ms.entry_id
           WHERE ms.user_id = ? AND ms.finished_at IS NULL ORDER BY ms.id DESC LIMIT 1""",
        (cfg.user_id,)).fetchone()
    return {"session": _mock_row(r) if r else None, "server_now": _now()}


@app.post("/api/mock/{session_id}/finish")
def api_mock_finish(session_id: int, body: MockFinish,
                    con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    s = con.execute("SELECT * FROM mock_sessions WHERE id = ? AND user_id = ?",
                    (session_id, cfg.user_id)).fetchone()
    if s is None:
        raise HTTPException(404, "not found")
    if s["finished_at"] is not None:
        raise HTTPException(409, "bereits beendet")
    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - datetime.fromisoformat(s["started_at"])).total_seconds()))
    finished = now.isoformat(timespec="seconds")
    con.execute("UPDATE mock_sessions SET finished_at = ?, seconds_taken = ?, score = ? WHERE id = ? AND user_id = ?",
                (finished, seconds, body.score, session_id, cfg.user_id))
    con.execute(
        """INSERT INTO progress(user_id, entry_id, status, score, notes, done_on, updated_at)
           VALUES (?, ?, 'erledigt', ?, '', ?, ?)
           ON CONFLICT(user_id, entry_id) DO UPDATE SET
             status = 'erledigt', score = COALESCE(excluded.score, progress.score),
             done_on = excluded.done_on, updated_at = excluded.updated_at""",
        (cfg.user_id, s["entry_id"], body.score, date.today().isoformat(), finished))
    con.commit()
    return {"ok": True, "seconds_taken": seconds, "score": body.score, "entry_id": s["entry_id"]}


@app.get("/api/mock")
def api_mock_history(limit: int | None = None, offset: int | None = None,
                     con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    lim, off = clamp_limit(limit, 20, 100), clamp_offset(offset)
    rows = con.execute(
        """SELECT ms.id, ms.entry_id, ms.planned_minutes, ms.started_at, ms.finished_at, ms.seconds_taken, ms.score,
                  e.title, e.subject, e.semester
           FROM mock_sessions ms LEFT JOIN entries e ON e.id = ms.entry_id
           WHERE ms.user_id = :uid AND ms.finished_at IS NOT NULL
           ORDER BY ms.id DESC LIMIT :lim OFFSET :off""", {"uid": cfg.user_id, "lim": lim, "off": off}).fetchall()
    return {"items": [_mock_row(r) for r in rows], "limit": lim, "offset": off}


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #

@app.get("/api/stats")
def api_stats(limit: int | None = None,
              con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    lim = clamp_limit(limit, 100)
    uid = {"uid": cfg.user_id, "lim": lim}
    weeks = con.execute(
        """SELECT strftime('%G-W%V', done_on) AS week, COUNT(*) AS done FROM progress
           WHERE user_id = :uid AND status = 'erledigt' AND done_on IS NOT NULL
           GROUP BY week ORDER BY week DESC LIMIT :lim""", uid).fetchall()
    modules = con.execute(
        """SELECT e.subject, COUNT(*) AS total,
                  SUM(COALESCE(p.status, 'offen') = 'erledigt') AS done,
                  ROUND(AVG(CASE WHEN p.status = 'erledigt' THEN p.score END), 1) AS avg_score,
                  SUM(p.status = 'erledigt' AND p.score IS NOT NULL) AS scored
           FROM entries e LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid
           GROUP BY e.subject ORDER BY e.subject LIMIT :lim""", uid).fetchall()
    lecturers = con.execute(
        """SELECT ln.name, ln.module, COUNT(DISTINCT e.id) AS total,
                  COUNT(DISTINCT CASE WHEN p.status = 'erledigt' THEN e.id END) AS done
           FROM lecturer_names ln
           JOIN entries e ON e.subject = ln.module AND e.semester = ln.semester
           LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid
           GROUP BY ln.name, ln.module ORDER BY total DESC, ln.name LIMIT :lim""", uid).fetchall()
    sidecar = con.execute(
        """SELECT el.name, e.subject AS module, COUNT(DISTINCT e.id) AS total,
                  COUNT(DISTINCT CASE WHEN p.status = 'erledigt' THEN e.id END) AS done
           FROM entry_lecturers el
           JOIN entries e ON e.id = el.entry_id
           LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid
           GROUP BY el.name, e.subject ORDER BY total DESC, el.name LIMIT :lim""", uid).fetchall()
    examiners = con.execute(
        """SELECT x.professor AS name, x.course AS module, COUNT(DISTINCT e.id) AS total,
                  COUNT(DISTINCT CASE WHEN p.status = 'erledigt' THEN e.id END) AS done
           FROM (SELECT DISTINCT professor, course, semester FROM examiners) x
           JOIN entries e ON e.subject = x.course AND e.semester = x.semester
           LEFT JOIN progress p ON p.entry_id = e.id AND p.user_id = :uid
           GROUP BY x.professor, x.course ORDER BY total DESC, x.professor LIMIT :lim""", uid).fetchall()
    return {
        "my_subjects": [m.subject for m in cfg.my_modules],
        "per_week": [dict(r) for r in weeks],
        "per_module": [dict(r) for r in modules],
        "per_lecturer": {"source_file": "lecturer_map.csv", "label": "hat gelesen (laut Vorlesungsverzeichnis)",
                         "items": [dict(r) for r in lecturers]},
        "per_sidecar_lecturer": {"source_file": "klausurwerk.json", "label": "Dozent laut klausurwerk.json",
                                 "items": [dict(r) for r in sidecar]},
        "per_examiner": {"source_file": "professor_map.csv", "label": LABEL_NAMED_EXAMINER,
                         "items": [dict(r) for r in examiners]},
        "limit": lim,
    }


# --------------------------------------------------------------------------- #
# Sources page
# --------------------------------------------------------------------------- #

@app.get("/api/quellen")
def api_quellen(cfg: Config = Depends(get_config)):
    path = cfg.collection_root / "Weitere-Quellen.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"available": False, "html": "", "file": "Weitere-Quellen.md"}
    if len(text) > 2_000_000:
        raise HTTPException(413, "too large")
    return {"available": True, "html": markdown.render(text), "file": "Weitere-Quellen.md"}


# --------------------------------------------------------------------------- #
# File serving — traversal-proof, fails closed
# --------------------------------------------------------------------------- #

def _not_found() -> HTTPException:
    return HTTPException(404, "not found")


@app.get("/files/{rel_path:path}")
def serve_file(rel_path: str, ocr: bool = False,
               con: sqlite3.Connection = Depends(get_db), cfg: Config = Depends(get_config)):
    try:
        row = con.execute("SELECT entry_id, role, rel_path, is_pdf, has_ocr_pdf FROM files WHERE rel_path = ?",
                          (rel_path,)).fetchone()
        if row is None:
            raise _not_found()
        if row["role"] == "loesung" and _active_mock(con, cfg.user_id, row["entry_id"]) is not None:
            raise HTTPException(403, "Lösung gesperrt, solange die Probeklausur läuft.")
        root = cfg.collection_root.resolve(strict=True)
        base = (root / OCR_DIR).resolve(strict=True) if ocr else root
        if ocr and not (row["is_pdf"] and row["has_ocr_pdf"]):
            raise _not_found()
        full = (base / row["rel_path"]).resolve(strict=True)
        full.relative_to(base)            # raises ValueError when outside
        full.relative_to(root)
        if not ocr and OCR_DIR in full.relative_to(root).parts[:1]:
            raise _not_found()
        if not full.is_file():
            raise _not_found()
    except HTTPException:
        raise
    except Exception:
        raise _not_found()
    if row["is_pdf"]:
        return FileResponse(full, media_type="application/pdf",
                            headers={"Content-Disposition": "inline", "Cache-Control": "private, no-store"})
    return FileResponse(full, media_type="application/octet-stream", filename=full.name,
                        headers={"Cache-Control": "private, no-store"})


# --------------------------------------------------------------------------- #
# Frontend
# --------------------------------------------------------------------------- #

@app.get("/")
def index_page():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.exception_handler(sqlite3.Error)
async def sqlite_error(_request: Request, _exc: sqlite3.Error):
    return JSONResponse({"detail": "Datenbankfehler"}, status_code=500)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
