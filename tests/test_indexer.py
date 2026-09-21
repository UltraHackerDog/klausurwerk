from app.config import load_config
from app.db import connect
from app.index import run


def test_indexer_counts_on_fixture(indexed):
    c = indexed["counts"]
    assert c["entries"] == 2
    assert c["files"] == 3
    assert c["entries_semester_unknown"] == 1
    assert c["pages_indexed"] == 4            # 2 + 1 + 1 page recovered from the _ocr copy
    assert c["files_with_ocr_copy"] == 1
    assert c["files_text_from_ocr"] == 1
    assert c["files_without_text"] == 0
    assert c["lecturer_rows"] == 1 and c["examiner_rows"] == 1
    assert indexed["text"]["extracted"] == 3


def test_indexer_is_incremental(indexed, env):
    second = run(load_config(), verbose=False)
    assert second["text"]["extracted"] == 0
    assert second["text"]["skipped_unchanged"] == 3
    assert second["counts"] == indexed["counts"]


def test_indexer_reextracts_changed_file_and_drops_removed(indexed, env):
    import json, os
    target = env / "Testfach/Klausuren/testfach_ws2021-2022_klausur_01_loesung.pdf"
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    manifest = json.loads((env / "manifest.json").read_text())
    (env / "manifest.json").write_text(json.dumps(manifest[:1]))
    res = run(load_config(), verbose=False)
    assert res["text"]["extracted"] == 1
    assert res["catalogue"]["removed_entries"] == 1
    assert res["counts"]["entries"] == 1 and res["counts"]["pages_indexed"] == 3


def test_progress_survives_reindex_and_carries_user_id(client, env):
    r = client.put("/api/entries/testfach_ws2021-2022_klausur_01/progress",
                   json={"status": "erledigt", "score": 80, "difficulty": 3, "notes": "ok"})
    assert r.status_code == 200 and r.json()["done_on"]
    run(load_config(), verbose=False)
    con = connect(load_config().db_path)
    row = con.execute("SELECT user_id, status, score FROM progress").fetchone()
    con.close()
    assert tuple(row) == ("local", "erledigt", 80)


def test_collection_is_not_written_to(env, indexed):
    names = sorted(p.name for p in env.iterdir())
    assert names == sorted(["Testfach", "_ocr", "geheim.txt", "lecturer_map.csv", "manifest.json",
                            "professor_map.csv", "readability.csv", "Weitere-Quellen.md"])


def test_extra_manifests_pair_solutions_and_never_parse_free_text(indexed, env):
    from tests.conftest import make_pdf, write_csv
    t = "Testfach/Ferienkurs-Archiv/2020w_fk_XX_testexam.pdf"
    ts = "Testfach/Ferienkurs-Archiv/2020w_fk_XX_testexamsolution.pdf"
    g = "GitHub/someone__repo/notes/week01.pdf"
    for rel in (t, ts, g):
        make_pdf(env / rel, ["Inhalt"])
    write_csv(env / "manifest_wayback.csv",
              ["subject", "semester", "type", "file", "status", "bytes", "sha256", "original_url", "archive_url", "retrieved_at"],
              [["Testfach", "2020w", "testexam", t, "ok", "1", "aa", "https://example.invalid/o", "https://example.invalid/a", "2026-09-17T00:00:00+00:00"],
               ["Testfach", "2020w", "testexamsolution", ts, "ok", "1", "bb", "", "", "2026-09-17T00:00:00+00:00"],
               ["Testfach", "2020w", "extra", "", "failed: HTTP 404", "", "", "", "", ""]])
    write_csv(env / "manifest_github.csv",
              ["repo", "ref", "repo_path", "file", "status", "bytes", "sha256", "git_blob_sha", "source_url", "retrieved_at", "source_set", "label"],
              [["someone/repo", "abc", "notes/week01.pdf", g, "ok", "1", "cc", "dd", "https://example.invalid/g", "2026-09-21T00:00:00+00:00",
                "notes", "Filename claims WS2022/23, but the title page says otherwise"]])
    res = run(load_config(), verbose=False)
    assert res["counts"]["entries"] == 4 and res["counts"]["files"] == 6

    con = connect(load_config().db_path)
    wb = con.execute("SELECT * FROM entries WHERE origin = 'manifest_wayback.csv'").fetchone()
    assert (wb["semester"], wb["kind"], wb["has_solution"]) == ("WS2020/2021", "exam", 1)
    assert "2020w" in wb["semester_note"]                      # the notation change is recorded
    gh = con.execute("SELECT * FROM entries WHERE origin = 'manifest_github.csv'").fetchone()
    con.close()
    # A free-text remark that happens to mention a semester must not become the semester.
    assert gh["semester"] == "????" and gh["sem_sort"] is None
    assert gh["subject"] == "GitHub" and gh["kind"] == "material"
    assert gh["source_note"].startswith("Filename claims")
    assert gh["title"] == "week01.pdf (notes)"
