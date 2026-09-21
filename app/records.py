"""Catalogue records shared by the manifest reader and the folder scanner."""
from __future__ import annotations

from dataclasses import dataclass, field

# Where a derived value came from. Shown in the UI next to the value.
FROM_MANIFEST = "manifest"
FROM_FOLDER = "folder"
FROM_FILENAME = "filename"
FROM_SIDECAR = "sidecar"
FROM_DEFAULT = "default"        # nothing recognised: the stated fallback, not a finding
FROM_COMPUTED = "computed"      # sha256 calculated by the scanner


@dataclass
class FileRec:
    role: str                   # angabe | loesung
    rel_path: str
    bytes: int | None = None
    sha256: str | None = None
    url: str | None = None
    archive_url: str | None = None
    retrieved_at: str | None = None
    mtime_ns: int | None = None
    sha256_from: str | None = None
    role_from: str | None = None
    role_note: str | None = None


@dataclass
class EntryRec:
    id: str
    subject: str
    semester: str
    title: str
    kind: str
    origin: str
    semester_raw: str | None = None
    semester_note: str | None = None
    kind_note: str | None = None
    source_note: str | None = None
    source_page: str | None = None
    retrieved_at: str | None = None
    subject_from: str | None = None
    semester_from: str | None = None
    kind_from: str | None = None
    title_from: str | None = None
    lecturers: list[str] = field(default_factory=list)
    lecturers_source_file: str | None = None
    files: list[FileRec] = field(default_factory=list)
