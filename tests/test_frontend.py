"""Gates on the no-build frontend: colour comes from the vendored Wasser tokens only."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
VENDOR = STATIC / "vendor"

# Same literals the kit's own gate refuses (scripts/no-raw-hex.mjs), plus the newer colour functions.
LITERAL = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(\s*[\d.+-]|\bcolor\(\s*[a-z]"
)
# CSS named colours. `transparent`, `currentColor` and `inherit` carry no hue and stay allowed.
NAMED = (
    "aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown "
    "burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan "
    "darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid "
    "darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet "
    "deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro "
    "ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki "
    "lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow "
    "lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray "
    "lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine "
    "mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise "
    "mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab "
    "orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru "
    "pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown "
    "seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan "
    "teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen"
).split()
NAMED_RE = "|".join(NAMED)
# A named colour as (part of) a CSS declaration value …
CSS_NAMED = re.compile(rf":[^;{{}}]*?(?<![\w-])({NAMED_RE})(?![\w-])", re.IGNORECASE)
# … and, in JS/HTML, as a whole quoted string or a presentation attribute.
QUOTED_NAMED = re.compile(rf"""(['"`])\s*({NAMED_RE})\s*\1""", re.IGNORECASE)


def _own_files():
    for path in sorted(STATIC.rglob("*")):
        if path.is_file() and path.suffix in {".css", ".js", ".html"} and VENDOR not in path.parents:
            yield path


def _strip_comments(text: str, suffix: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    if suffix == ".js":
        text = re.sub(r"(?m)(^|\s)//.*$", "", text)
    if suffix == ".html":
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text


def find_colour_literals(text: str, suffix: str) -> list[str]:
    hits = []
    for number, line in enumerate(_strip_comments(text, suffix).splitlines(), start=1):
        found = LITERAL.search(line)
        if not found:
            found = (CSS_NAMED if suffix == ".css" else QUOTED_NAMED).search(line)
        if found:
            hits.append(f"{number}: {line.strip()}")
    return hits


def test_no_colour_literal_outside_the_vendored_tokens():
    files = list(_own_files())
    assert any(p.name == "app.css" for p in files) and any(p.suffix == ".js" for p in files)
    failures = [f"{p.relative_to(STATIC)}:{hit}" for p in files for hit in find_colour_literals(p.read_text("utf-8"), p.suffix)]
    assert not failures, "Colour literals outside app/static/vendor/ (use var(--token)):\n" + "\n".join(failures)


def test_the_colour_gate_actually_bites():
    # A gate that cannot fail proves nothing: each of these must be caught …
    for bad, suffix in [
        (".a { color: #fff; }", ".css"),
        (".a { background: rgb(0 0 0 / 0.5); }", ".css"),
        (".a { color: hsl(200, 50%, 50%); }", ".css"),
        (".a { border: 1px solid red; }", ".css"),
        (".a { color: oklch(0.7 0.1 200); }", ".css"),
        ("el.style.color = 'white';", ".js"),
        ("el.style.background = '#0e6a62';", ".js"),
        ('<path fill="black">', ".html"),
    ]:
        assert find_colour_literals(bad, suffix), bad
    # … and token use, hash routes and hue-free keywords must pass.
    for good, suffix in [
        (".a { color: var(--text-1); background: color-mix(in srgb, var(--bg) 84%, transparent); }", ".css"),
        (".a { white-space: nowrap; border-color: transparent; }", ".css"),
        ("location.hash = '#/faecher'; const x = '#inhalt';", ".js"),
        ('<svg fill="none" stroke="currentColor">', ".html"),
    ]:
        assert not find_colour_literals(good, suffix), good


def test_wasser_kit_is_vendored_and_served(client):
    for rel in ["src/tokens.css", "src/base.css", "fonts/inter-latin-var.woff2", "fonts/jetbrains-mono-latin-var.woff2",
                "fonts/Inter-OFL.txt", "fonts/JetBrainsMono-OFL.txt", "VENDORED.md"]:
        assert (VENDOR / "wasser" / rel).is_file(), rel
    page = client.get("/").text
    for href in ["/static/vendor/wasser/src/tokens.css", "/static/vendor/wasser/src/base.css", "/static/css/app.css", "/static/js/ground.js"]:
        assert href in page and client.get(href).status_code == 200, href
    # base.css points at ../fonts/ — the vendored layout must make that resolve.
    assert client.get("/static/vendor/wasser/fonts/inter-latin-var.woff2").status_code == 200
    # Every token app.css names must be declared by the vendored contract (an unknown var() renders invisible).
    declared = set(re.findall(r"(--[\w-]+)\s*:", (VENDOR / "wasser" / "src" / "tokens.css").read_text("utf-8")))
    own = (STATIC / "css" / "app.css").read_text("utf-8")
    local = set(re.findall(r"(--_[\w-]+)\s*:", own))
    used = set(re.findall(r"var\(\s*(--[\w-]+)", own))
    assert used - declared - local == set(), sorted(used - declared - local)
