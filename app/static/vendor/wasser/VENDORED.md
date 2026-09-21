# Vendored: @strohmann-tum/wasser

- Package: `@strohmann-tum/wasser`
- Kit version: 2.0.0
- Commit: af9c264
- Copied on: 2026-09-21

## Licence of these files

`src/tokens.css` and `src/base.css` are © Strohmann. The package carries no
open-source licence. The two files are included here with permission and are
**not covered by this repository's MIT licence**: the MIT grant in `LICENSE`
does not extend to them, and permission to reuse them elsewhere has to come
from Strohmann.

The two fonts, Inter and JetBrains Mono, are under the SIL Open Font License
1.1; their licence texts are `fonts/Inter-OFL.txt` and
`fonts/JetBrainsMono-OFL.txt`.

| Here | From the kit |
|---|---|
| `src/tokens.css` | `src/tokens.css` |
| `src/base.css` | `src/base.css` |
| `fonts/inter-latin-var.woff2` | `fonts/inter-latin-var.woff2` |
| `fonts/jetbrains-mono-latin-var.woff2` | `fonts/jetbrains-mono-latin-var.woff2` |
| `fonts/Inter-OFL.txt` | `fonts/Inter-OFL.txt` |
| `fonts/JetBrainsMono-OFL.txt` | `fonts/JetBrainsMono-OFL.txt` |

All six files are unmodified, byte-identical copies. The `src/` and `fonts/`
folders mirror the kit's layout so that the `../fonts/…` paths inside
`base.css` resolve without editing it.

Do not edit these files. To update, copy them again from the kit and change the
version, commit and date above. Klausurwerk's own styles live in
`app/static/css/app.css` and use only the tokens declared in `tokens.css`;
`tests/test_frontend.py` fails if a colour literal appears outside this folder.
