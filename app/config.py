"""Configuration loading.

Everything the app needs to know about its surroundings comes from
`config.json` in the app directory (git-ignored: it is personal). When it does
not exist yet, the committed `config.example.json` is read instead, whose
placeholder `collection_root` produces a clear error. Three environment variables override
paths so tests never touch the real collection or the real database:

  KLAUSURWERK_CONFIG  path to a config.json
  KLAUSURWERK_ROOT    collection root (read-only input)
  KLAUSURWERK_DB      path to the SQLite file
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = APP_DIR / "config.json"
EXAMPLE_CONFIG = APP_DIR / "config.example.json"
DEFAULT_SCAN_MAX_FILES = 50_000
# ".nosync" keeps macOS from syncing this folder to iCloud when the app lives under ~/Documents:
# iCloud syncing a live SQLite file produces conflict copies and can corrupt it.
DEFAULT_DB = APP_DIR / "data.nosync" / "klausurwerk.sqlite"


@dataclass(frozen=True)
class Module:
    name: str
    subject: str
    lecturers: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    collection_root: Path
    db_path: Path
    user_id: str = "local"
    current_semester: str | None = None
    my_modules_source: str | None = None
    my_modules: tuple[Module, ...] = field(default_factory=tuple)
    scan_max_files: int = DEFAULT_SCAN_MAX_FILES     # folder-scan mode stops here and says so
    config_file: Path | None = None                  # the file that was actually read


def load_config() -> Config:
    override = os.environ.get("KLAUSURWERK_CONFIG")
    cfg_path = Path(override) if override else (DEFAULT_CONFIG if DEFAULT_CONFIG.is_file() else EXAMPLE_CONFIG)
    raw: dict = {}
    if cfg_path.is_file():
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RuntimeError(f"{cfg_path}: expected a JSON object")

    root = os.environ.get("KLAUSURWERK_ROOT") or raw.get("collection_root")
    if not root:
        raise RuntimeError("collection_root is not configured")
    db_path = Path(os.environ.get("KLAUSURWERK_DB", DEFAULT_DB))

    max_files = raw.get("scan_max_files", DEFAULT_SCAN_MAX_FILES)
    if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 1:
        raise RuntimeError("scan_max_files must be a positive whole number")

    modules = []
    for m in raw.get("my_modules") or []:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name", "")).strip()
        subject = str(m.get("subject", name)).strip()
        if not name or not subject:
            continue
        lecturers = tuple(str(x).strip() for x in m.get("lecturers", []) if str(x).strip())
        modules.append(Module(name=name, subject=subject, lecturers=lecturers))

    return Config(
        collection_root=Path(root).expanduser().resolve(),
        db_path=db_path,
        user_id=str(raw.get("user_id", "local")) or "local",
        current_semester=raw.get("current_semester"),
        my_modules_source=raw.get("my_modules_source"),
        my_modules=tuple(modules),
        scan_max_files=max_files,
        config_file=cfg_path if cfg_path.is_file() else None,
    )


def root_problem(cfg: Config) -> str | None:
    """A sentence for the user when the collection folder cannot be used, else None."""
    if cfg.collection_root.is_dir():
        return None
    where = cfg.config_file.name if cfg.config_file else "config.json"
    hint = ""
    if cfg.config_file == EXAMPLE_CONFIG:
        hint = " Copy config.example.json to config.json first."
    return (f"collection_root does not exist: {cfg.collection_root} (read from {where}).{hint} "
            f"Set \"collection_root\" in config.json to the folder that holds your PDFs.")
