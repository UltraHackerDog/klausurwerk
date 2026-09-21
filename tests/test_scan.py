"""Folder-scan mode. Every PDF here is generated; no real material is involved."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app import semesters
from app.scan import derive_kind, derive_semester, entry_id, parse_sidecar, readable_title, scan_collection, split_role, SidecarError
from tests.conftest import make_pdf


# ---- derivation tables ------------------------------------------------------ #
@pytest.mark.parametrize("stem, folders, kind, origin", [
    ("Klausur_WS2021-22", [], "exam", "filename"),
    ("Probeklausur", [], "exam", "filename"),
    ("Nachklausur 2019", [], "exam", "filename"),
    ("Wiederholungsklausur", [], "exam", "filename"),
    ("semestrale_2020", [], "exam", "filename"),
    ("Prüfung SS2019", [], "exam", "filename"),
    ("pruefung_ss2019", [], "exam", "filename"),
    ("midterm-2020", [], "exam", "filename"),
    ("Final Exam 2021", [], "exam", "filename"),
    ("mock_01", [], "exam", "filename"),
    ("Test 3", [], "exam", "filename"),
    ("exams", [], "exam", "filename"),
    ("Übungsblatt 03", [], "exercise", "filename"),
    ("uebung05", [], "exercise", "filename"),
    ("Blatt_7", [], "exercise", "filename"),
    ("sheet-04", [], "exercise", "filename"),
    ("Exercise 2", [], "exercise", "filename"),
    ("homework3", [], "exercise", "filename"),
    ("Hausaufgaben_3", [], "exercise", "filename"),
    ("Tutorium 5", [], "exercise", "filename"),
    ("tutorial_05", [], "exercise", "filename"),
    ("Problem Set 4", [], "exercise", "filename"),
    ("problem_set_4", [], "exercise", "filename"),
    ("Vorlesungsskript", [], "material", "filename"),
    ("script", [], "material", "filename"),
    ("lecture notes", [], "material", "filename"),
    ("Notizen", [], "material", "filename"),
    ("Zusammenfassung", [], "material", "filename"),
    ("summary_v2", [], "material", "filename"),
    ("Formelsammlung", [], "material", "filename"),
    ("cheatsheet", [], "material", "filename"),
    ("slides_week1", [], "material", "filename"),
    ("Folien_Kapitel3", [], "material", "filename"),
    # the folder speaks only when the file name says nothing; the nearest folder first
    ("ws2122", ["Klausuren", "Physik"], "exam", "folder"),
    ("03", ["Übungen", "Physik"], "exercise", "folder"),
    ("kapitel1", ["alt", "Skript", "Klausuren"], "material", "folder"),
    ("Formelsammlung", ["Klausuren"], "material", "filename"),
])
def test_kind_is_derived_from_keywords(stem, folders, kind, origin):
    got_kind, got_origin, note = derive_kind(stem, folders)
    assert (got_kind, got_origin) == (kind, origin)
    assert "Stichwort" in note                                # the matched keyword is recorded


@pytest.mark.parametrize("stem, folders", [
    ("example_data", []),              # "exam" inside another word
    ("Testat", ["Testfach"]),          # "test" inside another word, also in the folder
    ("latest", []),
    ("transcript", []),                # "script" inside another word
    ("worksheet", []),
    ("finally", []),
    ("scan_0001", ["Physik"]),
    ("Übungsklausur", []),             # two kinds in one name: not resolved
    ("Klausur_Zusammenfassung", []),
    ("ws2122", ["Klausuren und Übungen"]),
])
def test_kind_stays_unrecognised(stem, folders):
    kind, origin, note = derive_kind(stem, folders)
    assert (kind, origin) == ("material", "default")
    assert "nicht erkannt" in note


@pytest.mark.parametrize("text, semester", [
    ("WS2021/22", "WS2021/2022"),
    ("WS 2021-2022", "WS2021/2022"),
    ("WS2021_2022", "WS2021/2022"),
    ("ws2122", "WS2021/2022"),
    ("WS21-22", "WS2021/2022"),
    ("ws9900", "WS1999/2000"),
    ("WiSe21", "WS2021/2022"),
    ("WiSe 2019", "WS2019/2020"),
    ("Wintersemester 2019", "WS2019/2020"),
    ("SS2019", "SS2019"),
    ("SoSe 2019", "SS2019"),
    ("sose19", "SS2019"),
    ("Sommersemester 2019", "SS2019"),
    ("2021w", "WS2021/2022"),
    ("2019s", "SS2019"),
    ("2021-2022", "WS2021/2022"),
    ("2021_22", "WS2021/2022"),
    ("IN2003 Klausur SS2019", "SS2019"),       # the module code is not a year
    ("Klausur_WS2021-22_2022-02-14", "WS2021/2022"),   # a date inside the term fits
])
def test_semester_notations(text, semester):
    found = semesters.parse_free(text)
    assert found.semester == semester and not found.ambiguous
    assert found.matched and found.matched.lower() in text.lower()      # the matched substring is reported
    assert semesters.sort_key(found.semester) is not None               # canonical form


@pytest.mark.parametrize("text, year", [("Klausur_2019", 2019), ("2021-10-05 scan", 2021), ("Blatt 3 (2020)", 2020)])
def test_bare_year_is_year_only(text, year):
    found = semesters.parse_free(text)
    assert (found.semester, found.year, found.ambiguous) == (None, year, False)


@pytest.mark.parametrize("text", [
    "", "Klausur", "Blatt_03", "MA2003_klausur", "PH0001", "Klausur2021", "class2019", "news2021",
    "20211005", "1234", "2150", "scan_0007",
])
def test_no_semester_is_read_where_none_is_written(text):
    found = semesters.parse_free(text)
    assert (found.semester, found.year) == (None, None) and not found.ambiguous


@pytest.mark.parametrize("text", [
    "ws2021",                          # WS 2020/21 or WS 2021/22
    "WS 2021",
    "SS 21-22",                        # a summer term with a winter range
    "WS2021-22 und SS2019",
    "Klausur 2019 oder 2020",
    "WS2021-22_Klausur_2019",          # term and year contradict each other
])
def test_ambiguous_semester_stays_unknown(text):
    found = semesters.parse_free(text)
    assert found.ambiguous and found.semester is None and found.year is None
    sem, raw, origin, note = derive_semester(text, [])
    assert sem == semesters.UNKNOWN and origin is None and "nicht" in note
    assert semesters.display(sem) == semesters.UNKNOWN_LABEL


def test_semester_levels_nearest_term_wins_and_contradictions_stay_unknown():
    assert derive_semester("klausur", ["WS2019-20", "Physik"])[:3] == ("WS2019/2020", "WS2019-20", "folder")
    assert derive_semester("klausur_ss2018", ["WS2019-20"])[:3] == ("SS2018", "ss2018", "filename")
    assert derive_semester("klausur_2020", ["WS2019-20"])[0] == "WS2019/2020"          # 2020 lies inside that winter
    sem, raw, origin, note = derive_semester("klausur_2017", ["WS2019-20"])
    assert (sem, origin) == (semesters.UNKNOWN, None) and "2017" in note
    sem, raw, origin, note = derive_semester("klausur_2017", ["Physik"])
    assert (sem, raw, origin) == (semesters.UNKNOWN, "2017", None) and "Jahr 2017" in note
    assert derive_semester("klausur", ["Physik"]) == (semesters.UNKNOWN, None, None, None)


@pytest.mark.parametrize("stem, base, role", [
    ("klausur_ws2122", "klausur_ws2122", "angabe"),
    ("klausur_ws2122_loesung", "klausur_ws2122", "loesung"),
    ("klausur_ws2122_lösung", "klausur_ws2122", "loesung"),
    ("Klausur_WS2122_LSG", "klausur_ws2122", "loesung"),
    ("klausur_ws2122_lsgn", "klausur_ws2122", "loesung"),
    ("klausur_ws2122_sol", "klausur_ws2122", "loesung"),
    ("klausur_ws2122_solution", "klausur_ws2122", "loesung"),
    ("klausur_ws2122_solutions", "klausur_ws2122", "loesung"),
    ("klausur_ws2122_musterloesung", "klausur_ws2122", "loesung"),
    ("klausur_ws2122_ml", "klausur_ws2122", "loesung"),
    ("klausur_ws2122-loesung", "klausur_ws2122", "loesung"),
    ("Klausur WS2122 Lösung", "klausur ws2122", "loesung"),
    ("loesung_klausur_ws2122", "klausur_ws2122", "loesung"),          # prefix
    ("klausur_loesung_ws2122", "klausur_ws2122", "loesung"),          # infix
    ("klausur_ws2122_angabe", "klausur_ws2122", "angabe"),
    ("klausur_ws2122_aufgaben", "klausur_ws2122", "angabe"),
    ("Lösung", "", "loesung"),                                        # pairs with "Angabe" in the same folder
    ("Angabe", "", "angabe"),
    # not markers
    ("ML_Klausur_2021", "ml_klausur_2021", "angabe"),                 # "ML" in front is a subject, not a solution
    ("ml", "ml", "angabe"),
    ("sol_system", "sol_system", "angabe"),
    ("Klausurlösung", "klausurlösung", "angabe"),                     # no separator: not split
    ("resolution", "resolution", "angabe"),
    ("Hausaufgaben_3", "hausaufgaben_3", "angabe"),
])
def test_solution_markers(stem, base, role):
    got_base, got_role, marker = split_role(stem)
    assert (got_base, got_role) == (base, role)
    assert (marker is None) == (got_base == stem.lower())


def test_title_is_the_stem_made_readable_nothing_more():
    assert readable_title("klausur_ws2122_angabe") == "klausur ws2122 angabe"
    assert readable_title("Übungs-Blatt__03") == "Übungs-Blatt 03"


def test_entry_id_is_deterministic_and_collision_safe():
    assert entry_id("Physik/Klausuren/ws2122") == entry_id("Physik/Klausuren/ws2122")
    assert entry_id("Physik/a b") != entry_id("Physik/a_b")           # same slug, different hash
    assert entry_id("Physik/a b").rsplit("-", 1)[0] == entry_id("Physik/a_b").rsplit("-", 1)[0]
    assert len(entry_id("x/" + "sehr-langer-name-" * 30)) <= 80


# ---- a scanned collection --------------------------------------------------- #
FILES = {
    "Physik/Klausuren/klausur_ws2021-22.pdf": ["Aufgabe Pendel Drehimpuls"],
    "Physik/Klausuren/klausur_ws2021-22_loesung.pdf": ["Loesung Pendel Ergebnis"],
    "Physik/Klausuren/SS2019/Angabe.pdf": ["Aufgabe Wirbelstrom"],
    "Physik/Klausuren/SS2019/Lösung.pdf": ["Loesung Wirbelstrom"],
    "Physik/Übungen/Blatt_03.pdf": ["Blatt drei Zentrifugalkraft"],
    "Mathe/probeklausur_2020_lsg.pdf": ["nur die Loesung"],            # a solution without its paper
    "Mathe/scan_0007.pdf": [""],                                       # nothing recognisable, no text layer
    "lose_datei.pdf": ["liegt direkt im Wurzelordner"],
}


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "papers"
    for rel, pages in FILES.items():
        make_pdf(root / rel, pages)
    make_pdf(root / "_ocr" / "Mathe" / "scan_0007.pdf", ["Texterkennung Eigenwert"])
    make_pdf(root / ".versteckt" / "geheim.pdf", ["versteckt"])
    (root / "Mathe" / "notizen.txt").write_text("kein PDF", encoding="utf-8")
    (root / ".DS_Store").write_bytes(b"x")
    return root


@pytest.fixture()
def scan_env(folder: Path, tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"collection_root": str(folder), "user_id": "local", "my_modules": []}), encoding="utf-8")
    monkeypatch.setenv("KLAUSURWERK_CONFIG", str(cfg))
    monkeypatch.setenv("KLAUSURWERK_ROOT", str(folder))
    monkeypatch.setenv("KLAUSURWERK_DB", str(tmp_path / "data" / "scan.sqlite"))
    return folder


def _run(**kw):
    from app.config import load_config
    from app.index import run
    return run(load_config(), verbose=False, **kw)


def _rows(sql: str, args: tuple = ()):
    from app.config import load_config
    from app.db import connect
    con = connect(load_config().db_path)
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _entry_of(rel_path: str) -> dict:
    return _rows("SELECT e.* FROM entries e JOIN files f ON f.entry_id = e.id WHERE f.rel_path = ?", (rel_path,))[0]


@pytest.fixture()
def scan_client(scan_env):
    _run()
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_scan_builds_the_catalogue(scan_env):
    res = _run()
    assert res["mode"] == "scan"
    rep, counts = res["scan"], res["counts"]
    assert rep["pdfs_catalogued"] == 8 and counts["files"] == 8
    assert counts["entries"] == 6 and rep["entries_paired"] == 2 and rep["solutions_without_paper"] == 1
    assert rep["other_files"] == 1 and rep["other_extensions"] == {".txt": 1}
    assert rep["skipped_hidden"] == 2 and rep["cap_hit"] is False
    assert rep["sha256_computed"] == 8 and rep["sha256_reused"] == 0
    assert counts["files_with_ocr_copy"] == 1 and counts["files_text_from_ocr"] == 1
    assert counts["files_without_text"] == 0 and counts["subjects"] == 3

    paired = _entry_of("Physik/Klausuren/klausur_ws2021-22_loesung.pdf")
    assert paired == _entry_of("Physik/Klausuren/klausur_ws2021-22.pdf")
    assert (paired["subject"], paired["subject_from"]) == ("Physik", "folder")
    assert (paired["kind"], paired["kind_from"]) == ("exam", "filename")
    assert (paired["semester"], paired["semester_from"], paired["semester_raw"]) == ("WS2021/2022", "filename", "ws2021-22")
    assert (paired["title"], paired["title_from"], paired["has_solution"]) == ("klausur ws2021-22", "filename", 1)
    assert paired["origin"] == "folder-scan"

    by_folder = _entry_of("Physik/Klausuren/SS2019/Lösung.pdf")
    assert by_folder == _entry_of("Physik/Klausuren/SS2019/Angabe.pdf")
    assert (by_folder["semester"], by_folder["semester_from"]) == ("SS2019", "folder")
    assert (by_folder["kind"], by_folder["kind_from"], by_folder["title"]) == ("exam", "folder", "Angabe")

    lonely = _entry_of("Mathe/probeklausur_2020_lsg.pdf")
    assert (lonely["has_solution"], lonely["kind"]) == (1, "exam")
    assert (lonely["semester"], lonely["semester_from"], lonely["sem_sort"]) == ("????", None, None)   # a year is not a term
    assert "Jahr 2020" in lonely["semester_note"]
    assert [f["role"] for f in _rows("SELECT role FROM files WHERE entry_id = ?", (lonely["id"],))] == ["loesung"]

    blank = _entry_of("Mathe/scan_0007.pdf")
    assert (blank["kind"], blank["kind_from"], blank["semester"], blank["semester_note"]) == ("material", "default", "????", None)
    assert "nicht erkannt" in blank["kind_note"]

    loose = _entry_of("lose_datei.pdf")
    assert (loose["subject"], loose["subject_from"]) == ("Unsortiert", "default")


def test_fingerprint_is_the_real_sha256_and_rescans_are_incremental(scan_env):
    import hashlib
    _run()
    row = _rows("SELECT sha256, sha256_from, bytes FROM files WHERE rel_path = 'Physik/Übungen/Blatt_03.pdf'")[0]
    data = (scan_env / "Physik/Übungen/Blatt_03.pdf").read_bytes()
    assert row == {"sha256": hashlib.sha256(data).hexdigest(), "sha256_from": "computed", "bytes": len(data)}

    second = _run()
    assert second["scan"]["sha256_computed"] == 0 and second["scan"]["sha256_reused"] == 8
    assert second["text"]["extracted"] == 0 and second["text"]["skipped_unchanged"] == 8

    make_pdf(scan_env / "Physik/Übungen/Blatt_03.pdf", ["neuer Inhalt Corioliskraft"])
    third = _run()
    assert third["scan"]["sha256_computed"] == 1 and third["text"]["extracted"] == 1


def test_ids_are_stable_and_progress_survives_a_rescan(scan_client, scan_env):
    before = {r["id"] for r in _rows("SELECT id FROM entries")}
    eid = _entry_of("Physik/Klausuren/klausur_ws2021-22.pdf")["id"]
    r = scan_client.put(f"/api/entries/{eid}/progress", json={"status": "erledigt", "score": 70, "notes": "ok"})
    assert r.status_code == 200
    lonely = _entry_of("Mathe/probeklausur_2020_lsg.pdf")["id"]

    make_pdf(scan_env / "Mathe/probeklausur_2020.pdf", ["die Angabe taucht spaeter auf"])   # the paper arrives later
    make_pdf(scan_env / "Mathe/neu_klausur.pdf", ["neu"])
    _run()
    after = {r["id"] for r in _rows("SELECT id FROM entries")}
    assert before < after and len(after) == len(before) + 1
    assert _entry_of("Mathe/probeklausur_2020.pdf")["id"] == lonely                 # same entry, now with its paper
    assert _rows("SELECT user_id, entry_id, status, score FROM progress") == [
        {"user_id": "local", "entry_id": eid, "status": "erledigt", "score": 70}]
    assert scan_client.get(f"/api/entries/{eid}").json()["progress"]["status"] == "erledigt"

    # Renaming a file changes its id (documented); the progress row is kept, not moved.
    (scan_env / "Physik/Klausuren/klausur_ws2021-22.pdf").rename(scan_env / "Physik/Klausuren/umbenannt.pdf")
    _run()
    assert _entry_of("Physik/Klausuren/umbenannt.pdf")["id"] != eid
    assert [p["entry_id"] for p in _rows("SELECT entry_id FROM progress")] == [eid]


def test_scan_never_writes_to_the_collection(scan_env):
    def snapshot():
        return sorted((str(p.relative_to(scan_env)), p.stat().st_size, p.stat().st_mtime_ns)
                      for p in scan_env.rglob("*") if p.is_file())
    before = snapshot()
    _run()
    _run()
    assert snapshot() == before
    assert sorted(p.name for p in scan_env.iterdir()) == sorted([".DS_Store", ".versteckt", "Mathe", "Physik", "_ocr", "lose_datei.pdf"])


def test_symlinks_are_never_followed(scan_env, tmp_path):
    outside = tmp_path / "draussen"
    make_pdf(outside / "fremde_klausur.pdf", ["ausserhalb der Sammlung"])
    (scan_env / "Extern").symlink_to(outside, target_is_directory=True)
    (scan_env / "Mathe" / "verlinkt_klausur.pdf").symlink_to(outside / "fremde_klausur.pdf")
    res = _run()
    assert res["scan"]["skipped_symlinks"] == 2 and res["counts"]["files"] == 8
    assert _rows("SELECT 1 FROM files WHERE rel_path LIKE '%fremde%' OR rel_path LIKE '%verlinkt%'") == []
    assert _rows("SELECT 1 FROM pages WHERE text LIKE '%ausserhalb%'") == []


def test_scan_cap_is_reported_not_silent(scan_env, tmp_path, capsys):
    cfg = Path(os.environ["KLAUSURWERK_CONFIG"])
    cfg.write_text(json.dumps({"collection_root": str(scan_env), "scan_max_files": 3}), encoding="utf-8")
    from app.config import load_config
    from app.index import run
    res = run(load_config(), verbose=True)
    assert res["scan"]["cap_hit"] is True and res["scan"]["max_files"] == 3
    assert res["scan"]["files_seen"] == 3 and res["counts"]["files"] <= 3
    out = capsys.readouterr().out
    assert "abgebrochen" in out and "unvollständig" in out

    cfg.write_text(json.dumps({"collection_root": str(scan_env), "scan_max_files": 0}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_config()


def test_identical_conflict_copy_is_skipped_a_different_one_is_kept(scan_env):
    import shutil
    shutil.copy(scan_env / "Physik/Übungen/Blatt_03.pdf", scan_env / "Physik/Übungen/Blatt_03 2.pdf")
    make_pdf(scan_env / "Mathe/Klausur.pdf", ["erste Klausur"])
    make_pdf(scan_env / "Mathe/Klausur 2.pdf", ["zweite Klausur, anderer Inhalt"])
    make_pdf(scan_env / "Mathe/Einzeln 2.pdf", ["kein Original daneben"])
    res = _run()
    assert res["scan"]["skipped_identical_conflict_copies"] == 1
    paths = {r["rel_path"] for r in _rows("SELECT rel_path FROM files")}
    assert "Physik/Übungen/Blatt_03 2.pdf" not in paths
    assert {"Mathe/Klausur.pdf", "Mathe/Klausur 2.pdf", "Mathe/Einzeln 2.pdf"} <= paths


def test_pairing_never_crosses_folders_and_extras_stand_alone(tmp_path):
    root = tmp_path / "p"
    make_pdf(root / "A/klausur.pdf", ["a"])
    make_pdf(root / "B/klausur_loesung.pdf", ["b"])
    make_pdf(root / "C/klausur.pdf", ["c"])
    make_pdf(root / "C/klausur_loesung.pdf", ["c1"])
    make_pdf(root / "C/klausur_lsg.pdf", ["c2"])
    entries, rep = scan_collection(root)
    shape = sorted(sorted(f.rel_path for f in e.files) for e in entries)
    assert shape == [["A/klausur.pdf"], ["B/klausur_loesung.pdf"], ["C/klausur.pdf", "C/klausur_loesung.pdf"], ["C/klausur_lsg.pdf"]]
    assert len({e.id for e in entries}) == 4
    extra = [e for e in entries if e.files[0].rel_path == "C/klausur_lsg.pdf"][0]
    assert "eigener Eintrag" in extra.files[0].role_note


# ---- sidecar ---------------------------------------------------------------- #
def test_sidecar_overrides_and_records_its_source(scan_env):
    (scan_env / "Mathe" / "klausurwerk.json").write_text(json.dumps({
        "defaults": {"subject": "Lineare Algebra", "semester": "SS 2020", "lecturers": ["Anna Beispiel"]},
        "files": {"scan_0007.pdf": {"title": "Klausur (Scan)", "kind": "exam", "semester": "WS2019/2020"}},
    }), encoding="utf-8")
    make_pdf(scan_env / "Mathe/handschrift.pdf", ["eine Loesung ohne Kennzeichen"])
    (scan_env / "Physik" / "Übungen" / "klausurwerk.json").write_text(json.dumps({
        "defaults": {"kind": "exercise"}}), encoding="utf-8")
    res = _run()
    assert res["scan"]["sidecars_applied"] == 2 and res["scan"]["sidecars_rejected"] == []

    e = _entry_of("Mathe/scan_0007.pdf")
    assert (e["subject"], e["subject_from"]) == ("Lineare Algebra", "sidecar")
    assert (e["title"], e["title_from"]) == ("Klausur (Scan)", "sidecar")
    assert (e["kind"], e["kind_from"]) == ("exam", "sidecar")
    assert (e["semester"], e["semester_from"]) == ("WS2019/2020", "sidecar")       # the file beats the folder default
    other = _entry_of("Mathe/probeklausur_2020_lsg.pdf")
    assert (other["semester"], other["semester_from"], other["semester_raw"]) == ("SS2020", "sidecar", "SS 2020")
    assert "klausurwerk.json" in other["semester_note"]
    assert (other["kind"], other["kind_from"]) == ("exam", "filename")             # not overridden: still derived
    assert _entry_of("Physik/Klausuren/klausur_ws2021-22.pdf")["subject_from"] == "folder"   # other folders untouched
    names = _rows("SELECT name, source, source_file FROM entry_lecturers WHERE entry_id = ?", (e["id"],))
    assert names == [{"name": "Anna Beispiel", "source": "sidecar", "source_file": "Mathe/klausurwerk.json"}]


def test_sidecar_solution_of_pairs_files_the_names_do_not(scan_env):
    make_pdf(scan_env / "Mathe/handschrift.pdf", ["eine Loesung ohne Kennzeichen"])
    (scan_env / "Mathe" / "klausurwerk.json").write_text(json.dumps({
        "files": {"handschrift.pdf": {"solution_of": "scan_0007.pdf"}}}), encoding="utf-8")
    _run()
    e = _entry_of("Mathe/handschrift.pdf")
    assert e == _entry_of("Mathe/scan_0007.pdf") and e["has_solution"] == 1
    f = _rows("SELECT role, role_from, role_note FROM files WHERE rel_path = 'Mathe/handschrift.pdf'")[0]
    assert (f["role"], f["role_from"]) == ("loesung", "sidecar") and "scan_0007.pdf" in f["role_note"]


@pytest.mark.parametrize("content", [
    "{ not json",
    "[]",
    json.dumps({"default": {}}),                                                   # unknown top-level key
    json.dumps({"defaults": {"kind": "quiz"}}),
    json.dumps({"defaults": {"semester": "irgendwann"}}),
    json.dumps({"defaults": {"semester": "ws2021"}}),                              # ambiguous stays refused
    json.dumps({"defaults": {"lecturers": "Anna Beispiel"}}),
    json.dumps({"defaults": {"subject": "Gut"}, "files": {"gibt_es_nicht.pdf": {"kind": "exam"}}}),
    json.dumps({"defaults": {"subject": "Gut"}, "files": {"scan_0007.pdf": {"kind": "exam", "farbe": "rot"}}}),
    json.dumps({"files": {"scan_0007.pdf": {"solution_of": "scan_0007.pdf"}}}),
    json.dumps({"files": {"scan_0007.pdf": {"solution_of": "../Physik/x.pdf"}}}),
    json.dumps({"files": {"scan_0007.pdf": {"solution_of": "probeklausur_2020_lsg.pdf", "title": "x"}}}),
])
def test_malformed_sidecar_is_skipped_whole_and_reported(scan_env, content, capsys):
    (scan_env / "Mathe" / "klausurwerk.json").write_text(content, encoding="utf-8")
    from app.config import load_config
    from app.index import run
    res = run(load_config(), verbose=True)
    rejected = res["scan"]["sidecars_rejected"]
    assert [r["file"] for r in rejected] == ["Mathe/klausurwerk.json"] and rejected[0]["error"]
    assert "Mathe/klausurwerk.json" in capsys.readouterr().out                      # named in the indexer summary
    # never half-applied: the valid-looking "subject" of a rejected file is not used either
    e = _entry_of("Mathe/scan_0007.pdf")
    assert (e["subject"], e["subject_from"], e["kind_from"]) == ("Mathe", "folder", "default")
    assert res["counts"]["entries"] == 6                                            # and the scan went on


def test_parse_sidecar_unit():
    ok = parse_sidecar(json.dumps({"files": {"a.pdf": {"solution_of": None}}}), "klausurwerk.json", {"a.pdf"})
    assert ok.files == {"a.pdf": {"solution_of": None}}
    with pytest.raises(SidecarError):
        parse_sidecar(json.dumps({"files": {"a.pdf": {"solution_of": "b.pdf"}, "c.pdf": {"solution_of": "b.pdf"}}}),
                      "klausurwerk.json", {"a.pdf", "b.pdf", "c.pdf"})


# ---- API over a scanned collection ------------------------------------------ #
def test_dashboard_without_modules_shows_every_subject_with_progress(scan_client):
    eid = _entry_of("Physik/Übungen/Blatt_03.pdf")["id"]
    scan_client.put(f"/api/entries/{eid}/progress", json={"status": "erledigt"})
    eid2 = _entry_of("Physik/Klausuren/SS2019/Angabe.pdf")["id"]
    scan_client.put(f"/api/entries/{eid2}/progress", json={"status": "in_arbeit"})
    body = scan_client.get("/api/dashboard").json()
    assert body["modules"] == [] and body["subjects_total"] == 3
    got = {s["subject"]: (s["total"], s["done"], s["in_progress"], s["exams"]) for s in body["subjects"]}
    assert got == {"Mathe": (2, 0, 0, 1), "Physik": (3, 1, 1, 2), "Unsortiert": (1, 0, 0, 0)}
    assert len(scan_client.get("/api/dashboard?limit=1").json()["subjects"]) == 1
    assert scan_client.get("/api/dashboard?limit=999999").json()["subjects_limit"] == 200


def test_dashboard_with_modules_has_no_subject_fallback(client):
    body = client.get("/api/dashboard").json()
    assert len(body["modules"]) == 1 and body["subjects"] == []


def test_entry_api_says_where_each_value_came_from(scan_client):
    eid = _entry_of("Physik/Klausuren/SS2019/Angabe.pdf")["id"]
    data = scan_client.get(f"/api/entries/{eid}").json()
    prov = data["provenance"]
    assert prov["subject"]["from"] == "folder" and prov["title"]["from"] == "filename"
    assert prov["kind"]["from"] == "folder" and "Klausuren" in prov["kind"]["note"]
    assert prov["semester"] == {"from": "folder", "note": prov["semester"]["note"], "as_written": "SS2019"}
    assert {f["role"]: (f["role_from"], f["sha256_from"]) for f in data["files"]} == {
        "angabe": ("filename", "computed"), "loesung": ("filename", "computed")}
    # lecturer features degrade: no maps, no sidecar -> unknown, not an error
    assert data["lecturers"] == {"rows": [], "source_file": "lecturer_map.csv", "known": False}
    assert data["examiners"]["known"] is False
    meta = scan_client.get("/api/meta").json()
    assert meta["index_mode"] == "scan" and meta["scan"]["pdfs_catalogued"] == 8
    assert "sidecar_lecturer" not in meta["labels"]
    assert scan_client.get("/api/lecturers").json()["items"] == []
    stats = scan_client.get("/api/stats").json()
    assert stats["per_lecturer"]["items"] == [] and stats["per_sidecar_lecturer"]["items"] == []

    unknown = scan_client.get(f"/api/entries/{_entry_of('Mathe/scan_0007.pdf')['id']}").json()
    assert unknown["provenance"]["semester"] == {"from": None, "note": None, "as_written": None}
    assert unknown["entry"]["semester_label"] == "Semester unbekannt"


def test_manifest_entries_report_manifest_as_their_source(client):
    data = client.get("/api/entries/testfach_ws2021-2022_klausur_01").json()
    assert {k: v["from"] for k, v in data["provenance"].items()} == dict.fromkeys(["subject", "title", "kind", "semester"], "manifest")
    assert {f["sha256_from"] for f in data["files"]} == {"manifest"}
    assert client.get("/api/meta").json()["index_mode"] == "manifest"


def test_sidecar_lecturers_feed_the_lecturer_ui(scan_env, tmp_path):
    (scan_env / "Physik" / "klausurwerk.json").write_text(json.dumps({
        "defaults": {"lecturers": ["Anna Beispiel"]}}), encoding="utf-8")
    cfg = Path(os.environ["KLAUSURWERK_CONFIG"])
    cfg.write_text(json.dumps({"collection_root": str(scan_env), "my_modules": [
        {"name": "Physik", "subject": "Physik", "lecturers": ["anna beispiel"]}]}), encoding="utf-8")
    _run()
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/lecturers").json()["items"] == [{"name": "Anna Beispiel", "source_kind": "sidecar"}]
        assert c.get("/api/entries", params={"dozent": "Anna Beispiel"}).json()["total"] == 3
        item = c.get("/api/entries", params={"subject": "Physik"}).json()["items"][0]
        assert item["lecturers"] == "Anna Beispiel"
        mod = c.get("/api/dashboard").json()["modules"][0]
        assert mod["totals"]["priority_total"] == 3
        assert mod["papers"][0]["reasons"] == ["gleicher Dozent (laut klausurwerk.json)"]
        assert c.get("/api/meta").json()["labels"]["sidecar_lecturer"] == "gleicher Dozent (laut klausurwerk.json)"
        eid = _entry_of("Physik/Übungen/Blatt_03.pdf")["id"]           # semester unknown, lecturer still shown
        rows = c.get(f"/api/entries/{eid}").json()["lecturers"]["rows"]
        assert rows == [{"lecturers": "Anna Beispiel", "source": "sidecar", "caveat": None, "recorded_at": None,
                         "source_file": "Physik/klausurwerk.json"}]
        assert c.get("/api/stats").json()["per_sidecar_lecturer"]["items"] == [
            {"name": "Anna Beispiel", "module": "Physik", "total": 3, "done": 0}]


def test_scanned_files_are_served_and_nothing_else(scan_client):
    assert scan_client.get("/files/Physik/Klausuren/SS2019/Angabe.pdf").status_code == 200
    assert scan_client.get("/files/Mathe/scan_0007.pdf?ocr=1").status_code == 200
    for path in ["Mathe/notizen.txt", ".versteckt/geheim.pdf", "_ocr/Mathe/scan_0007.pdf", "../config.json"]:
        assert scan_client.get("/files/" + path).status_code == 404
    hit = scan_client.get("/api/search", params={"q": "eigenwert"}).json()
    assert hit["total"] == 1 and hit["items"][0]["rel_path"] == "Mathe/scan_0007.pdf"


def test_mock_exam_works_on_a_scanned_entry(scan_client):
    eid = _entry_of("Physik/Klausuren/klausur_ws2021-22.pdf")["id"]
    sid = scan_client.post("/api/mock/start", json={"entry_id": eid, "minutes": 60}).json()["id"]
    assert scan_client.get("/files/Physik/Klausuren/klausur_ws2021-22_loesung.pdf").status_code == 403
    assert scan_client.post(f"/api/mock/{sid}/finish", json={"score": 55}).status_code == 200
    assert scan_client.get("/files/Physik/Klausuren/klausur_ws2021-22_loesung.pdf").status_code == 200


# ---- mode selection fails closed --------------------------------------------- #
def test_scan_flag_wins_over_a_manifest_and_a_lost_manifest_does_not_switch_modes(indexed, env):
    assert indexed["mode"] == "manifest"
    (env / "manifest.json").rename(env / "manifest.json.weg")
    with pytest.raises(SystemExit) as exc:
        _run()
    assert "--scan" in str(exc.value) and "untouched" in str(exc.value)
    assert {r["origin"] for r in _rows("SELECT origin FROM entries")} == {"manifest.json"}      # old index intact
    res = _run(scan=True)
    assert res["mode"] == "scan" and {r["origin"] for r in _rows("SELECT origin FROM entries")} == {"folder-scan"}


def test_empty_or_missing_folder_leaves_the_index_alone(scan_env, tmp_path, monkeypatch):
    _run()
    empty = tmp_path / "leer"
    empty.mkdir()
    monkeypatch.setenv("KLAUSURWERK_ROOT", str(empty))
    with pytest.raises(SystemExit):
        _run()
    monkeypatch.setenv("KLAUSURWERK_ROOT", str(tmp_path / "gibt-es-nicht"))
    with pytest.raises(SystemExit) as exc:
        _run()
    assert "collection_root" in str(exc.value)
    assert len(_rows("SELECT id FROM entries")) == 6


def test_app_refuses_to_start_without_a_collection_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("KLAUSURWERK_CONFIG", raising=False)
    monkeypatch.setenv("KLAUSURWERK_ROOT", str(tmp_path / "gibt-es-nicht"))
    monkeypatch.setenv("KLAUSURWERK_DB", str(tmp_path / "db.sqlite"))
    from fastapi.testclient import TestClient
    from app.main import app
    with pytest.raises(RuntimeError) as exc:
        with TestClient(app):
            pass
    assert "collection_root" in str(exc.value) and "config.json" in str(exc.value)


def test_example_config_is_neutral_and_is_the_fallback(tmp_path, monkeypatch):
    from app import config
    example = json.loads(config.EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert example["my_modules"] == [] and example["user_id"] == "local"
    assert example["collection_root"] == "/path/to/your/papers"
    monkeypatch.delenv("KLAUSURWERK_CONFIG", raising=False)
    monkeypatch.delenv("KLAUSURWERK_ROOT", raising=False)
    monkeypatch.setattr(config, "DEFAULT_CONFIG", tmp_path / "config.json")          # a fresh clone: no config.json
    cfg = config.load_config()
    assert cfg.config_file == config.EXAMPLE_CONFIG and cfg.my_modules == ()
    problem = config.root_problem(cfg)
    assert "config.example.json" in problem and "collection_root" in problem


def test_old_database_gets_the_new_columns_without_losing_rows(tmp_path):
    import sqlite3
    from app.db import connect, init_schema
    db = tmp_path / "alt.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE entries (id TEXT PRIMARY KEY, subject TEXT NOT NULL, semester TEXT NOT NULL, semester_raw TEXT,
            semester_note TEXT, sem_sort INTEGER, title TEXT NOT NULL, kind TEXT NOT NULL, kind_note TEXT,
            origin TEXT NOT NULL, source_page TEXT, retrieved_at TEXT, has_solution INTEGER NOT NULL DEFAULT 0,
            seen_run TEXT NOT NULL);
        CREATE TABLE files (id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL, role TEXT NOT NULL, rel_path TEXT NOT NULL UNIQUE,
            is_pdf INTEGER NOT NULL, bytes INTEGER, sha256 TEXT, url TEXT, archive_url TEXT, retrieved_at TEXT,
            on_disk INTEGER NOT NULL DEFAULT 0, has_ocr_pdf INTEGER NOT NULL DEFAULT 0, pages INTEGER, text_pages INTEGER,
            image_only_pages INTEGER, empty_pages INTEGER, chars INTEGER, clean_ratio REAL, verdict TEXT,
            idx_fingerprint TEXT, idx_text_source TEXT, idx_pages INTEGER, idx_pages_with_text INTEGER, idx_error TEXT,
            seen_run TEXT NOT NULL);
        INSERT INTO entries(id, subject, semester, title, kind, origin, seen_run) VALUES ('a', 'F', '????', 'T', 'exam', 'manifest.json', 'r');
    """)
    con.commit()
    con.close()
    con = connect(db)
    init_schema(con)
    init_schema(con)                                                                 # idempotent
    cols = {r["name"] for r in con.execute("PRAGMA table_info(entries)")}
    fcols = {r["name"] for r in con.execute("PRAGMA table_info(files)")}
    assert {"source_note", "subject_from", "semester_from", "kind_from", "title_from"} <= cols
    assert {"mtime_ns", "sha256_from", "role_from", "role_note"} <= fcols
    assert con.execute("SELECT id, subject_from FROM entries").fetchall()[0]["id"] == "a"
    assert con.execute("SELECT COUNT(*) FROM entry_lecturers").fetchone()[0] == 0
    con.close()
