"""Gate: nothing personal in tracked files.

Tracked text files (everything `git ls-files` lists, except uv.lock) must not
contain absolute home paths, the strings on the deny-list below, or the
lecturer names from the developer's own untracked config.json. The deny-list
strings are assembled from pieces so that this file passes its own check, and
the lecturer names are read at test time, never written here.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKIP_FILES = {"uv.lock"}

HOME_PATHS = [
    re.compile("/" + "Users" + r"/[^/\s\"'`<>]"),                 # macOS
    re.compile("/" + "home" + r"/[A-Za-z0-9._-]+/"),              # Linux
    re.compile(r"[A-Za-z]:\\+" + "Users" + r"\\"),                # Windows
]
DENY = ["dog" + "go1", "TUM" + "exams", "TUM-" + "Altklausuren", "TUM-" + "Lernplattform"]
TITLES = ("prof", "dr")


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("not a git checkout (or git is not installed)")
    names = [n for n in out.decode("utf-8", "replace").split("\0") if n]
    if not names:
        pytest.skip("git lists no tracked files")
    return [REPO / n for n in names if n not in SKIP_FILES]


def text_of(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None                                               # binary (fonts) or deleted but still listed


def name_patterns(lecturers: list[str]) -> list[re.Pattern]:
    """Patterns for configured lecturer names. A surname of five letters or more is matched on
    its own; a shorter one (it may be an ordinary word) only behind a title, an initial or the
    first name, and as the full configured name."""
    patterns = []
    for full in lecturers:
        tokens = [t for t in full.split() if t.rstrip(".").casefold() not in TITLES]
        if not tokens:
            continue
        surname = re.escape(tokens[-1])
        if len(tokens) > 1:
            patterns.append(re.compile(r"\b" + r"\s+".join(re.escape(t) for t in tokens) + r"\b"))
        if len(tokens[-1]) >= 5:
            patterns.append(re.compile(rf"\b{surname}\b"))
        else:
            first = re.escape(tokens[0]) if len(tokens) > 1 else r"[A-Z]\."
            patterns.append(re.compile(rf"(?:Prof\.|Dr\.|\b[A-Z]\.|\b{first})\s+{surname}\b"))
    return patterns


def find_hits(text: str, patterns: list[re.Pattern], words: list[str]) -> list[str]:
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        if any(p.search(line) for p in patterns) or any(w.casefold() in line.casefold() for w in words):
            hits.append(f"{number}: {line.strip()[:120]}")
    return hits


def test_no_home_path_or_denied_string_in_tracked_files():
    files = tracked_files()
    assert any(p.name == "README.md" for p in files)
    failures = [f"{p.relative_to(REPO)}:{hit}" for p in files
                for hit in find_hits(text_of(p) or "", HOME_PATHS, DENY)]
    assert not failures, "Personal paths or names in tracked files:\n" + "\n".join(failures)


def test_no_configured_lecturer_name_in_tracked_files():
    config = REPO / "config.json"
    if not config.is_file():
        pytest.skip("no local config.json to read names from")
    files = tracked_files()
    assert config not in files, "config.json is personal and must stay untracked"
    raw = json.loads(config.read_text(encoding="utf-8"))
    lecturers = [str(n) for m in raw.get("my_modules") or [] if isinstance(m, dict) for n in m.get("lecturers") or []]
    if not lecturers:
        pytest.skip("config.json names no lecturers")
    patterns = name_patterns(lecturers)
    failures = [f"{p.relative_to(REPO)}:{hit.split(':')[0]}" for p in files for hit in find_hits(text_of(p) or "", patterns, [])]
    # Line numbers only: the failure message must not repeat the names either.
    assert not failures, "A lecturer name from config.json appears in tracked files at:\n" + "\n".join(failures)


def test_personal_files_are_ignored_and_untracked():
    files = {p.relative_to(REPO).as_posix() for p in tracked_files()}
    assert "config.json" not in files and not any(f.startswith(".claude/") for f in files)
    assert not any(f.lower().endswith((".pdf", ".sqlite", ".apkg", ".zip")) for f in files)
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {"config.json", ".claude/", "*.pdf", "data.nosync/"} <= set(ignore)


def test_the_gate_actually_bites():
    home = "/" + "Users" + "/someone/Documents/papers"
    for bad in [f'"collection_root": "{home}"', "cd /" + "home" + "/someone/papers", "C:\\" + "Users" + "\\someone",
                "built on " + DENY[0] + "'s machine"]:
        assert find_hits(bad, HOME_PATHS, DENY), bad
    for good in ['"collection_root": "/path/to/your/papers"', "~/papers", "the home page", "Users can set a folder",
                 "https://example.invalid/Users"]:
        assert not find_hits(good, HOME_PATHS, DENY), good

    patterns = name_patterns(["Prof. Dr. Erika Beispielfrau", "Max Kurz"])
    for bad in ["asked Beispielfrau about it", "Prof. Dr. E. Beispielfrau", "Max Kurz", "Dr. Kurz", "M. Kurz"]:
        assert find_hits(bad, patterns, []), bad
    for good in ["Kurz gesagt", "kurz", "ein Beispiel", "Prof. Dr. A. Beispiel"]:
        assert not find_hits(good, patterns, []), good
