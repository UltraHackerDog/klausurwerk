"""Fixtures build a tiny throw-away collection. The real collection is never touched."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pymupdf as fitz
import pytest


def make_pdf(path: Path, pages: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 100), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture()
def collection(tmp_path: Path) -> Path:
    root = tmp_path / "sammlung"
    root.mkdir()
    a = "Testfach/Klausuren/testfach_ws2021-2022_klausur_01_angabe.pdf"
    l = "Testfach/Klausuren/testfach_ws2021-2022_klausur_01_loesung.pdf"
    u = "Testfach/Klausuren/testfach_unbekannt_klausur_01_angabe.pdf"
    sha_a = make_pdf(root / a, ["Aufgabe eins Pendel", "Aufgabe zwei Zentrifugalkraft Drehimpuls"])
    sha_l = make_pdf(root / l, ["Loesung Pendel Ergebnis"])
    sha_u = make_pdf(root / u, [""])                       # image-like page: no text layer
    # OCR copy for the text-less file; the indexer must prefer it for extraction.
    make_pdf(root / "_ocr" / u, ["Texterkennung Wirbelstrom"])
    # A file that exists on disk but is NOT in any manifest — must never be served.
    (root / "geheim.txt").write_text("nicht im Index", encoding="utf-8")
    outside = tmp_path / "ausserhalb.txt"
    outside.write_text("ausserhalb der Sammlung", encoding="utf-8")

    manifest = [
        {"subject": "Testfach", "year": "WS2021/2022", "id": "testfach_ws2021-2022_klausur_01",
         "title": "Klausur Testfach (WS2021/2022)", "kind": "exam",
         "angabe_file": a, "angabe_url": "https://example.invalid/a.pdf", "angabe_bytes": 1, "angabe_sha256": sha_a,
         "loesung_file": l, "loesung_url": "https://example.invalid/l.pdf", "loesung_bytes": 1, "loesung_sha256": sha_l,
         "source_page": "https://example.invalid/", "retrieved_at": "2026-09-17T13:51:25+00:00"},
        {"subject": "Testfach", "year": "????", "id": "testfach_unbekannt_klausur_01",
         "title": "Klausur Testfach (ohne Jahr)", "kind": "exam",
         "angabe_file": u, "angabe_url": None, "angabe_bytes": 1, "angabe_sha256": sha_u,
         "loesung_file": None, "source_page": "https://example.invalid/", "retrieved_at": "2026-09-17T13:51:25+00:00"},
    ]
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_csv(root / "readability.csv",
              ["file", "opens", "encrypted", "pages", "text_pages", "image_only_pages", "empty_pages", "chars", "clean_ratio", "verdict", "error"],
              [[a, "True", "False", "2", "2", "0", "0", "60", "0.9", "text", ""],
               [l, "True", "False", "1", "1", "0", "0", "20", "0.9", "text", ""],
               [u, "True", "False", "1", "0", "1", "0", "0", "0.0", "scan", ""]])
    write_csv(root / "lecturer_map.csv", ["module", "semester", "lecturers", "source", "caveat", "recorded_at"],
              [["Testfach", "WS2021/2022", "Erika Beispiel; Max Muster", "Testkatalog", "lists lecturers, not examiners", "2026-09-17T00:00:00+00:00"]])
    write_csv(root / "professor_map.csv",
              ["professor", "course", "exam_type", "year_as_listed", "semester", "listed_title", "source", "retrieved_at"],
              [["Prof. Dr. E. Beispiel", "Testfach", "Klausur", "2021/22", "WS2021/2022", "Testfach, Klausur, 2021/22", "Testquelle", "2026-09-17T00:00:00+00:00"]])
    (root / "Weitere-Quellen.md").write_text("# Titel\n\n<script>alert(1)</script> **fett** https://example.invalid/x\n", encoding="utf-8")
    return root


@pytest.fixture()
def env(collection: Path, tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "collection_root": str(collection), "user_id": "local", "current_semester": "WS2026/2027",
        "my_modules": [{"name": "Testfach", "subject": "Testfach", "lecturers": ["Erika Beispiel"]}],
    }), encoding="utf-8")
    monkeypatch.setenv("KLAUSURWERK_CONFIG", str(cfg))
    monkeypatch.setenv("KLAUSURWERK_ROOT", str(collection))
    monkeypatch.setenv("KLAUSURWERK_DB", str(tmp_path / "data" / "test.sqlite"))
    return collection


@pytest.fixture()
def indexed(env):
    from app.config import load_config
    from app.index import run
    return run(load_config(), verbose=False)


@pytest.fixture()
def client(indexed):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
