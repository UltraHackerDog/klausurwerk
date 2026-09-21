# Klausurwerk

A small local web app for revising with your own collection of past exam
papers. Point it at a folder of PDFs and it gives you a library you can browse
by subject and semester, full-text search, progress tracking with notes, timed
mock exams that keep the solution locked until you finish, and simple
statistics. It runs on your machine, talks to `127.0.0.1` only, and never
writes to your papers. The UI is in German; code and docs are in English.

## Quick start

You need [`uv`](https://docs.astral.sh/uv/) (it installs Python 3.11+ and the
dependencies). No Docker, no Node, no build step.

```sh
git clone <this repository> klausurwerk && cd klausurwerk
uv sync
cp config.example.json config.json      # then set "collection_root" to your folder
uv run python -m app.index              # build the catalogue and the search index
./run.sh                                # http://127.0.0.1:8765
```

If `collection_root` does not exist, both the indexer and the app stop with a
message that says which setting to change.

## Use it with your own papers

Put your PDFs in one folder. The first folder level is the subject; below that,
arrange things however you like:

```
papers/
  Analysis/
    Klausuren/
      Klausur_WS2021-22.pdf
      Klausur_WS2021-22_Loesung.pdf     <- paired with the paper above
      SS2019/
        Angabe.pdf                      <- semester and kind come from the folders
        Lösung.pdf
    Uebungen/
      Blatt_04.pdf
  Informatik/
    midterm_2019s.pdf
    klausurwerk.json                    <- optional, see "Sidecar"
  loose_file.pdf                        <- subject "Unsortiert"
  _ocr/                                 <- optional OCR copies, see "Scanned PDFs"
```

Run `uv run python -m app.index`. Without a `manifest.json` in the root the
indexer scans the folder tree (`--scan` forces this). It ends with a summary:
how many PDFs were catalogued, how many were paired, how many have no text,
which sidecars were rejected, and whether the file cap was hit.

### What is read from names

Nothing is invented. Every derived value records how it was obtained (folder,
file name, sidecar), the entry page shows that under "Herkunft", and whatever
cannot be derived is stored and shown as unknown.

| Value | Rule |
|---|---|
| Subject | The first folder under the root. Files directly in the root: "Unsortiert". |
| Kind | Keywords in the file name; if it has none, the nearest parent folder that has one. **exam**: klausur, probeklausur, nachklausur, wiederholungsklausur, semestrale, pruefung/prüfung, exam, midterm, final, mock, test. **exercise**: uebung/übung, blatt, hausaufgabe, homework, tutorium, tutorial, sheet, exercise, problem set. **material**: skript, script, notes, notizen, zusammenfassung, summary, formelsammlung, cheatsheet, slides, folien. German keywords match inside compounds ("Übungsblatt"); the short English ones only as whole words ("exam" does not fire on "example"). No keyword, or keywords of two kinds in one name ("Übungsklausur"): listed as material, marked "not recognised". |
| Semester | Explicit notations only, file name first, then the nearest folder: `WS2021/22`, `WS 2021-2022`, `ws2122`, `WS21-22`, `WiSe21`, `Wintersemester 2019`, `SS2019`, `SoSe 2019`, `ss19`, `2021w` / `2019s`, `2021-2022` or `2021_22` (winter). A bare year (`Klausur_2019`) gives a year but no term, so the semester stays unknown and the year is noted. |
| Paper + solution | Two files in the same folder whose names are equal once a marker is removed. Solution markers: `loesung`, `lösung`, `loesungen`, `lsg`, `lsgn`, `solution(s)`, `musterloesung`, and `sol` / `ml` at the end of the name. Question markers: `angabe`, `aufgaben`. A marker is a whole name part between `_`, `-`, space or `.`, in any position and any case. A solution without its paper becomes its own entry. Files in different folders are never paired. |
| Title | The file name without extension, underscores as spaces. Nothing else is changed. |
| Fingerprint | sha256 of the file, computed during the scan. A re-scan re-hashes only files whose size or modification time changed. |

What stays unknown on purpose:

- `ws2021` and `WS 2021` can mean WS 2020/21 or WS 2021/22. Write `ws2021-22` or `ws2122`.
- Two different semesters in one name, or a term that contradicts a year in the
  same path (`WS2019-20/klausur_2017.pdf`).
- Digits glued to letters (`MA2003`, `Klausur2021`) are not read as a year,
  because module codes look the same. Write `Klausur_2021`.
- Two-digit years follow the usual convention: 00–68 → 20xx, 69–99 → 19xx.
  The entry page says so when it was applied.

"Unknown" means exactly that: the app found nothing it is allowed to read a
value from. Such entries are listed and searchable, show "Semester unbekannt",
and are left out of semester-range filters.

### Entry ids and your progress

An entry's id is a slug of its relative path (without the solution marker) plus
a short hash, so re-indexing and re-scanning keep ids, and your progress and
notes stay attached. A paper or solution that arrives later joins the existing entry.
**Renaming or moving a file changes its id**: the old progress row is kept in
the database but no longer belongs to a listed entry. Fix metadata with a
sidecar instead of renaming once you have started tracking.

### Sidecar: better metadata without renaming files

A `klausurwerk.json` in any folder:

```json
{
  "defaults": {
    "subject": "Lineare Algebra",
    "kind": "exam",
    "semester": "SS2020",
    "lecturers": ["Prof. Dr. A. Beispiel"]
  },
  "files": {
    "scan_0007.pdf": { "title": "Klausur", "kind": "exam", "semester": "WS2019/2020" },
    "handwritten.pdf": { "solution_of": "scan_0007.pdf" },
    "ML_Klausur.pdf": { "solution_of": null }
  }
}
```

- `defaults` apply to the folder and the folders below it; a nearer sidecar
  wins per key. `files` entries apply to PDFs in the same folder and win over
  defaults. Sidecar values override derived ones and are shown as coming from
  the sidecar.
- `kind` is `exam`, `exercise` or `material`. `semester` is `WS2021/2022` /
  `SS2019` or any unambiguous notation from the table above.
- `solution_of` pairs two files whose names do not match; `null` says "this is
  not a solution" for a name that was misread. A file with `solution_of` takes
  title, kind and semester from its paper.
- `lecturers` feed the lecturer column, filter and statistics, labelled "laut
  klausurwerk.json".
- Validation is strict: unknown keys, wrong types, an unreadable semester, or a
  file name that is not a PDF in that folder reject the **whole** sidecar. The
  scan continues without it and the indexer summary names the file and the
  reason. A sidecar is never half-applied.

### Scanned PDFs

Search uses the PDF's text layer. A scan without one is still listed and opens
in the viewer; the summary counts such files (`files_without_text`). The app
ships no OCR. To make scans searchable, write OCR copies to `_ocr/` under the
same relative path, for example with [OCRmyPDF](https://ocrmypdf.readthedocs.io/):

```sh
cd /path/to/your/papers
find . -name '*.pdf' -not -path './_ocr/*' | while read -r f; do
  mkdir -p "_ocr/$(dirname "$f")"
  ocrmypdf --skip-text -l deu+eng "$f" "_ocr/$f"
done
```

Your originals are not touched. On the next index run the `_ocr` copy is used
for the text, and the viewer offers it as "OCR-Kopie".

### What the scan skips

- Symbolic links, files and folders alike. Nothing outside the root is read.
- Hidden files and folders (names starting with `.`) and the top-level `_ocr/`.
- `name 2.pdf` next to `name.pdf` when both are byte-identical (a sync conflict
  copy). A copy with different content is kept as its own entry.
- Anything that is not a `.pdf`; the summary counts those by extension.
- Everything after `scan_max_files` files (config, default 50000) and folders
  more than 16 levels deep. The indexer summary says so when that happens (so
  does the subject overview on the start page); the catalogue is then incomplete.

If a scan finds no PDF at all, or the folder is missing, the existing index is
left as it is.

## Configuration

`config.json` (git-ignored; start from `config.example.json`):

- `collection_root` — your folder of papers. Read-only input.
- `my_modules` — optional. Each item has a display `name`, the `subject` as it
  appears in the library, and this semester's `lecturers`. With modules set, the
  start page ranks each module's papers by matching lecturer. With an empty
  list it shows every subject with its progress. The example file carries one
  fictional module under `_example_my_modules`, a key the loader ignores; copy
  its shape into `my_modules`.
- `current_semester`, `user_id` (default `local`), `scan_max_files`.

Environment overrides (used by the tests): `KLAUSURWERK_CONFIG`,
`KLAUSURWERK_ROOT`, `KLAUSURWERK_DB`.

## Privacy

- Everything stays on your machine. The app binds to loopback, refuses requests
  with a foreign `Host` header, makes no external requests and has no telemetry.
  The only links that leave the machine are source URLs you click yourself.
- The PDFs stay where they are. The app does not copy them; it serves indexed
  paths from the collection folder and nothing else.
- Bring only material you are allowed to use, and do not publish it.
- Do not commit papers. `.gitignore` blocks `*.pdf`, `*.apkg`, `*.zip`, the
  database folder and `config.json`.

## Where data lives

| What | Where |
|---|---|
| Papers, sidecars, OCR copies | the collection folder (`collection_root`), read-only |
| Index, progress, notes, mock-exam history | `data.nosync/klausurwerk.sqlite` (git-ignored) |
| Your settings | `config.json` (git-ignored) |

The folder is called `data.nosync` on purpose: macOS does not sync folders with
that suffix to iCloud, and syncing a live SQLite file can corrupt it. Keep a copy of
`data.nosync/klausurwerk.sqlite` if your notes matter to you. Deleting it and
re-indexing rebuilds the catalogue and the search index, but not your progress.

## Manifest mode

A collection that was assembled by a download script can describe itself
instead of being scanned: a root with `manifest.json` (a list of entries with
`id`, `subject`, `year`, `title`, `kind`, `angabe_file`, `loesung_file`, URLs,
sizes and sha256) and the optional files `manifest_wayback.csv`,
`manifest_github.csv`, `readability.csv`, `lecturer_map.csv`,
`professor_map.csv` and `Weitere-Quellen.md`. `lecturer_map.csv` and
`professor_map.csv` are also read in scan mode when present.

- Values are shown as the manifests state them; the sha256 is the recorded one
  and is not recomputed.
- `lecturer_map.csv` lists who lectured; who set the exam is an inference, and
  the caveat from the file is shown next to the name. `professor_map.csv` is
  matched to entries by subject and semester only, and to your configured
  lecturers by surname plus first initial. Both rules are stated in the UI.
- A year of `????` is "Semester unbekannt"; `2013w` is converted to
  `WS2013/2014` and the conversion is shown. Free-text labels are kept verbatim
  as a note; nothing is derived from them.
- If `manifest.json` disappears from a collection that was indexed from it, the
  indexer stops and leaves the index as it is. Pass `--scan` to switch that
  collection to folder-scan mode; entry ids differ between the two modes.

## Indexer

```sh
uv run python -m app.index            # catalogue + per-page text (FTS5)
uv run python -m app.index --no-text  # catalogue only
uv run python -m app.index --scan     # scan folders even if a manifest.json exists
```

Re-runnable and incremental: a file's text is re-read only when its
fingerprint (sha256, size, mtime, and the mtime of its `_ocr` copy) changed.
Entries that disappear are dropped from the index; progress rows are kept.

## Design

The UI is built on the token contract of the Wasser design kit
(`app/static/vendor/wasser/`, see `VENDORED.md` there for version and commit):
one ground (paper by default, ink via `data-theme="ink"` on `<html>`; the
toggle in the top bar stores the choice, otherwise the system preference
decides), monochrome chrome, hue only for status and measures, stand-alone
numbers in the figure face, one filled control per surface.
`app/static/css/app.css` may not contain a colour literal — only `var(--token)`.

The two kit files `src/tokens.css` and `src/base.css` come from
`@strohmann-tum/wasser` 2.0.0. They are © Strohmann, included with permission,
and **not covered by this repository's MIT licence** (see Licence).

Klausurwerk is a personal project. It is not a Strohmann product and is not
affiliated with or endorsed by Strohmann; only those two files come from there.

## Development

```sh
uv run pytest
```

The tests build tiny throw-away collections (PDFs generated with PyMuPDF) and
never touch a real one. Two gates run with them:

- the colour-literal gate (`tests/test_frontend.py`): no colour literal outside
  `app/static/vendor/`, only `var(--token)`;
- the personal-data gate (`tests/test_no_personal_data.py`): no tracked text
  file (except `uv.lock`) may contain an absolute home path, a string on the
  test's deny-list, or a lecturer name from your own untracked `config.json`.
  It reads `git ls-files` and skips itself outside a git checkout.

```
app/config.py      config.json + env overrides
app/db.py          SQLite schema (FTS5)
app/index.py       the indexer (manifest mode, text extraction, summary)
app/scan.py        folder-scan mode: derivation tables, sidecar, the walk
app/records.py     catalogue records shared by both modes
app/semesters.py   semester parsing and display
app/main.py        FastAPI app: API, file serving, static frontend
app/markdown.py    tiny escaping Markdown renderer for the "Quellen" page
app/static/        index.html, css/app.css, js/ (ES modules, no build, no CDN)
tests/             pytest suite
```

### Engineering rules in this code base

- Every SQL statement is a static string with bound parameters. The only
  dynamic parts are `ORDER BY`, picked from an allow-list (`ENTRY_ORDER`), and
  the additive column list in `app/db.py`, a literal in the source.
- Every list endpoint has `LIMIT`/`OFFSET`; `limit` is clamped to 1…200. The
  folder scan has a file cap and a depth cap.
- `/files/<path>` serves only paths present in the index, resolves the real
  path and checks it is inside the collection root; everything else is 404.
  While a mock exam runs, its solution file answers 403.
- Progress and mock-exam rows carry a `user_id`.
- Schema changes are additive and applied by `init_schema`.

## Licence

The app's own code is under the MIT License, see `LICENSE`.

Not covered by that licence:

- `app/static/vendor/wasser/src/tokens.css` and `src/base.css`: © Strohmann,
  from `@strohmann-tum/wasser` 2.0.0, a package without an open-source licence.
  They are included with permission; the MIT grant does not extend to them.
- The fonts Inter and JetBrains Mono in `app/static/vendor/wasser/fonts/`: SIL
  Open Font License 1.1, licence texts alongside.

Dependencies are not bundled; `uv sync` installs them on your machine. The app
depends on **PyMuPDF**, which is dual-licensed: GNU AGPL 3.0 or a commercial
licence from Artifex. Running or redistributing Klausurwerk together with
PyMuPDF is subject to PyMuPDF's terms, not only to the MIT licence of this code.

Runtime dependencies and their licences, as each package's own metadata states
them (read with `importlib.metadata` from the versions in `uv.lock`):

| Package | Licence per its metadata |
|---|---|
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| pymupdf | "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License" |
| starlette | BSD-3-Clause |
| pydantic, pydantic_core | MIT |
| annotated-types, annotated-doc, typing-inspection | MIT |
| typing_extensions | PSF-2.0 |
| anyio | MIT |
| idna | BSD-3-Clause |
| click | BSD-3-Clause |
| h11 | MIT |

Read on macOS with Python 3.12; other platforms or Python versions can resolve
additional packages. Check them the same way before redistributing.
