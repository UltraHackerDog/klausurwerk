import pytest

from app import markdown, semesters
from app.main import MAX_LIMIT, clamp_limit, fts_query

ANGABE = "Testfach/Klausuren/testfach_ws2021-2022_klausur_01_angabe.pdf"
LOESUNG = "Testfach/Klausuren/testfach_ws2021-2022_klausur_01_loesung.pdf"
SCAN = "Testfach/Klausuren/testfach_unbekannt_klausur_01_angabe.pdf"


# ---- file endpoint ---------------------------------------------------------- #
def test_indexed_file_is_served(client):
    r = client.get(f"/files/{ANGABE}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


@pytest.mark.parametrize("path", [
    "../ausserhalb.txt",
    "..%2Fausserhalb.txt",
    "%2e%2e/%2e%2e/etc/passwd",
    "Testfach/../../ausserhalb.txt",
    "Testfach/Klausuren/../../geheim.txt",
    "/etc/passwd",
    "//etc/passwd",
    "geheim.txt",                  # on disk inside the root, but not in the index
    "manifest.json",
    "_ocr/" + SCAN,                # OCR copies are only reachable through ?ocr=1 of an indexed path
    "",
])
def test_traversal_and_unindexed_paths_get_404(client, path):
    r = client.get("/files/" + path)
    assert r.status_code == 404
    assert b"ausserhalb" not in r.content and b"nicht im Index" not in r.content


def test_symlink_escaping_the_root_is_refused(client, env, tmp_path):
    """An indexed path that resolves outside the collection root must fail closed."""
    target = env / ANGABE
    target.unlink()
    target.symlink_to(tmp_path / "ausserhalb.txt")
    assert client.get(f"/files/{ANGABE}").status_code == 404


def test_ocr_variant_only_when_a_copy_exists(client):
    assert client.get(f"/files/{SCAN}?ocr=1").status_code == 200
    assert client.get(f"/files/{ANGABE}?ocr=1").status_code == 404


def test_foreign_host_header_is_refused(client):
    assert client.get("/api/meta", headers={"host": "evil.example"}).status_code == 400


# ---- LIMIT clamping --------------------------------------------------------- #
def test_clamp_limit_unit():
    assert clamp_limit(None) == 50
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(10**9) == MAX_LIMIT
    assert clamp_limit(500, 20, 100) == 100


@pytest.mark.parametrize("url", ["/api/entries", "/api/subjects", "/api/semesters", "/api/lecturers",
                                 "/api/search?q=pendel", "/api/mock", "/api/dashboard", "/api/stats"])
def test_list_endpoints_clamp_limit(client, url):
    sep = "&" if "?" in url else "?"
    body = client.get(f"{url}{sep}limit=999999").json()
    assert 1 <= body["limit"] <= MAX_LIMIT
    assert client.get(f"{url}{sep}limit=-3").json()["limit"] == 1


def test_entries_limit_is_applied(client):
    body = client.get("/api/entries?limit=1").json()
    assert body["total"] == 2 and len(body["items"]) == 1


def test_sort_goes_through_allow_list(client):
    assert client.get("/api/entries?sort=title").status_code == 200
    assert client.get("/api/entries?sort=id;DROP TABLE entries").status_code == 400
    assert client.get("/api/entries").json()["total"] == 2


def test_sql_metacharacters_in_filters_are_plain_values(client):
    assert client.get("/api/entries", params={"subject": "x' OR '1'='1"}).json()["total"] == 0
    assert client.get("/api/entries", params={"q": "%"}).json()["total"] == 0
    assert client.get("/api/entries", params={"q": "ohne Jahr"}).json()["total"] == 1


# ---- search ----------------------------------------------------------------- #
def test_search_returns_the_right_page(client):
    body = client.get("/api/search", params={"q": "Zentrifugalkraft"}).json()
    assert body["total"] == 1
    hit = body["items"][0]
    assert hit["page_no"] == 2 and hit["rel_path"] == ANGABE and hit["role"] == "angabe"
    assert "Zentrifugalkraft" in hit["snippet"] and len(hit["snippet"]) <= 320


def test_search_uses_ocr_text_and_prefix(client):
    body = client.get("/api/search", params={"q": "wirbelstr"}).json()
    assert body["total"] == 1 and body["items"][0]["rel_path"] == SCAN
    assert body["items"][0]["semester_label"] == "Semester unbekannt"


def test_search_can_leave_out_solutions(client):
    assert client.get("/api/search", params={"q": "pendel"}).json()["total"] == 2
    assert client.get("/api/search", params={"q": "pendel", "include_solutions": "false"}).json()["total"] == 1


def test_search_survives_fts_syntax(client):
    for q in ['"', "AND OR NOT", "pendel*) OR (", "NEAR(", "a:b", "-", "   "]:
        assert client.get("/api/search", params={"q": q}).status_code == 200
    assert fts_query('pen"del') == '"pen" "del"*'
    assert fts_query("  ") is None


# ---- unknown stays unknown -------------------------------------------------- #
def test_unknown_semester_is_rendered_as_unknown(client):
    assert semesters.display("????") == "Semester unbekannt"
    assert semesters.display(None) == "Semester unbekannt"
    assert semesters.display("2020") == "Semester unbekannt"
    assert semesters.display("WS2021/2022") == "WS 2021/22"
    data = client.get("/api/entries/testfach_unbekannt_klausur_01").json()
    assert data["entry"]["semester_label"] == "Semester unbekannt"
    assert data["entry"]["semester_known"] is False
    assert data["entry"]["sem_sort"] is None
    assert data["lecturers"]["known"] is False and data["lecturers"]["rows"] == []
    assert data["examiners"]["known"] is False


def test_semester_range_never_guesses_a_place_for_unknown(client):
    ids = [i["id"] for i in client.get("/api/entries?sem_from=SS2000").json()["items"]]
    assert ids == ["testfach_ws2021-2022_klausur_01"]


def test_lecturer_facts_carry_their_source(client):
    data = client.get("/api/entries/testfach_ws2021-2022_klausur_01").json()
    assert data["lecturers"]["source_file"] == "lecturer_map.csv"
    assert data["lecturers"]["rows"][0]["caveat"] == "lists lecturers, not examiners"
    assert data["examiners"]["source_file"] == "professor_map.csv"
    assert data["examiners"]["rows"][0]["professor"] == "Prof. Dr. E. Beispiel"


def test_dashboard_ranks_own_lecturer_first(client):
    mod = client.get("/api/dashboard").json()["modules"][0]
    assert mod["totals"]["total"] == 2 and mod["totals"]["priority_total"] == 1
    first = mod["papers"][0]
    assert first["id"] == "testfach_ws2021-2022_klausur_01"
    assert first["reasons"] == ["gleicher Dozent (laut Vorlesungsverzeichnis)", "Prüfer laut Fachschaft"]
    assert mod["papers"][1]["reasons"] == []
    assert mod["matched_professor_spellings"] == ["Prof. Dr. E. Beispiel"]


# ---- mock exam -------------------------------------------------------------- #
def test_mock_exam_locks_solution_until_finished(client):
    eid = "testfach_ws2021-2022_klausur_01"
    sid = client.post("/api/mock/start", json={"entry_id": eid, "minutes": 90}).json()["id"]
    assert client.get(f"/files/{LOESUNG}").status_code == 403
    assert client.get(f"/files/{ANGABE}").status_code == 200
    assert client.get(f"/api/entries/{eid}").json()["solution_locked"] is True
    assert client.get("/api/search", params={"q": "Ergebnis"}).json()["total"] == 0
    assert client.post("/api/mock/start", json={"entry_id": eid}).status_code == 409
    done = client.post(f"/api/mock/{sid}/finish", json={"score": 72}).json()
    assert done["score"] == 72 and done["seconds_taken"] >= 0
    assert client.get(f"/files/{LOESUNG}").status_code == 200
    p = client.get(f"/api/entries/{eid}").json()["progress"]
    assert p["status"] == "erledigt" and p["score"] == 72
    stats = client.get("/api/stats").json()
    assert sum(w["done"] for w in stats["per_week"]) == 1
    assert [m for m in stats["per_module"] if m["subject"] == "Testfach"][0]["avg_score"] == 72.0


def test_progress_validation(client):
    url = "/api/entries/testfach_ws2021-2022_klausur_01/progress"
    assert client.put(url, json={"status": "erledigt", "score": 101}).status_code == 422
    assert client.put(url, json={"status": "fertig"}).status_code == 422
    assert client.put(url, json={"status": "offen", "difficulty": 6}).status_code == 422
    assert client.put("/api/entries/gibt-es-nicht/progress", json={"status": "offen"}).status_code == 404


# ---- markdown --------------------------------------------------------------- #
def test_markdown_never_injects_raw_html(client):
    html = client.get("/api/quellen").json()["html"]
    assert "<script" not in html and "&lt;script&gt;" in html
    assert "<strong>fett</strong>" in html
    assert '<a href="https://example.invalid/x"' in html
    out = markdown.render('| a | b |\n|---|---|\n| <img src=x onerror=1> | `c` |\n\n- javascript:alert(1)\n')
    assert "<img" not in out and "<table>" in out and "<code>c</code>" in out and "href=\"javascript" not in out


def test_index_page_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "Klausurwerk" in r.text
    assert "http://" not in r.text and "https://" not in r.text      # no CDN, no external asset
    assert client.get("/static/js/main.js").status_code == 200
