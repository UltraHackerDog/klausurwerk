"""SQLite connection and schema. All statements are static and parameterized."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One revisable item: an exam, an exercise sheet or course material.
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    semester TEXT NOT NULL,            -- canonical ('WS2021/2022', 'SS2019') or '????'
    semester_raw TEXT,                 -- as written in the source manifest
    semester_note TEXT,                -- how `semester` was derived, when it was
    sem_sort INTEGER,                  -- NULL when the semester is unknown
    title TEXT NOT NULL,
    kind TEXT NOT NULL,                -- exam | exercise | material
    kind_note TEXT,
    source_note TEXT,                  -- free-text remark from the manifest, shown verbatim
    origin TEXT NOT NULL,              -- manifest.json | manifest_wayback.csv | manifest_github.csv | folder-scan
    source_page TEXT,
    retrieved_at TEXT,
    has_solution INTEGER NOT NULL DEFAULT 0,
    seen_run TEXT NOT NULL,
    -- how each value was obtained: manifest | folder | filename | sidecar | default (NULL = not derivable)
    subject_from TEXT,
    semester_from TEXT,
    kind_from TEXT,
    title_from TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_subject ON entries(subject, sem_sort);
CREATE INDEX IF NOT EXISTS idx_entries_subject_semester ON entries(subject, semester);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                -- angabe | loesung
    rel_path TEXT NOT NULL UNIQUE,     -- relative to the collection root
    is_pdf INTEGER NOT NULL,
    bytes INTEGER,
    sha256 TEXT,
    url TEXT,
    archive_url TEXT,
    retrieved_at TEXT,
    on_disk INTEGER NOT NULL DEFAULT 0,
    has_ocr_pdf INTEGER NOT NULL DEFAULT 0,
    -- from readability.csv (NULL = not listed there)
    pages INTEGER,
    text_pages INTEGER,
    image_only_pages INTEGER,
    empty_pages INTEGER,
    chars INTEGER,
    clean_ratio REAL,
    verdict TEXT,
    -- text-index bookkeeping for incremental runs
    idx_fingerprint TEXT,
    idx_text_source TEXT,              -- original | ocr_pdf | ocr_txt (mixed: ocr_pdf+ocr_txt)
    idx_pages INTEGER,
    idx_pages_with_text INTEGER,
    idx_error TEXT,
    seen_run TEXT NOT NULL,
    mtime_ns INTEGER,                  -- scan mode: lets a re-scan skip hashing an unchanged file
    sha256_from TEXT,                  -- manifest (as recorded there) | computed (by the scanner)
    role_from TEXT,                    -- manifest | filename | sidecar
    role_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_entry ON files(entry_id);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE(file_id, page_no)
);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    text,
    content='pages',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
    INSERT INTO pages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

-- Who LECTURED (course catalogue). Who set the exam is an inference.
CREATE TABLE IF NOT EXISTS lecturer_rows (
    id INTEGER PRIMARY KEY,
    module TEXT NOT NULL,
    semester TEXT NOT NULL,
    lecturers TEXT NOT NULL,
    source TEXT,
    caveat TEXT,
    recorded_at TEXT
);
CREATE TABLE IF NOT EXISTS lecturer_names (
    row_id INTEGER NOT NULL REFERENCES lecturer_rows(id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    semester TEXT NOT NULL,
    name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lecturer_names ON lecturer_names(module, semester);

-- Lecturers a klausurwerk.json sidecar names for the entries of its folder (scan mode).
CREATE TABLE IF NOT EXISTS entry_lecturers (
    entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    source TEXT NOT NULL,              -- sidecar
    source_file TEXT,                  -- the sidecar, relative to the collection root
    PRIMARY KEY (entry_id, name)
);

-- Examiner as named by the student council, per listed exam.
CREATE TABLE IF NOT EXISTS examiners (
    id INTEGER PRIMARY KEY,
    professor TEXT NOT NULL,
    course TEXT NOT NULL,
    exam_type TEXT,
    year_as_listed TEXT,
    semester TEXT,
    listed_title TEXT,
    source TEXT,
    retrieved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_examiners ON examiners(course, semester);

-- Personal tracking. Multi-user by design; today the only user is 'local'.
CREATE TABLE IF NOT EXISTS progress (
    user_id TEXT NOT NULL DEFAULT 'local',
    entry_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'offen',   -- offen | in_arbeit | erledigt
    score INTEGER,                          -- 0..100, self-assessed
    difficulty INTEGER,                     -- 1..5
    notes TEXT NOT NULL DEFAULT '',
    done_on TEXT,                           -- YYYY-MM-DD
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, entry_id)
);

CREATE TABLE IF NOT EXISTS mock_sessions (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'local',
    entry_id TEXT NOT NULL,
    planned_minutes INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    seconds_taken INTEGER,
    score INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mock_user ON mock_sessions(user_id, finished_at);
"""


ADDED_COLUMNS = (
    ("entries", "source_note", "TEXT"),
    ("entries", "subject_from", "TEXT"),
    ("entries", "semester_from", "TEXT"),
    ("entries", "kind_from", "TEXT"),
    ("entries", "title_from", "TEXT"),
    ("files", "mtime_ns", "INTEGER"),
    ("files", "sha256_from", "TEXT"),
    ("files", "role_from", "TEXT"),
    ("files", "role_note", "TEXT"),
)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    # Additive columns for databases created before they existed (no data is rewritten).
    # Table and column names come from this literal allow-list, never from input.
    for table, column, decl in ADDED_COLUMNS:
        columns = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    con.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )
    con.commit()
