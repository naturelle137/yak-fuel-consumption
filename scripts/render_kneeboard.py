#!/usr/bin/env python3
"""Render kneeboard/kneeboard.html to a print-ready PDF without a browser.

The HTML builds its two A4 pages with a small inline script (three copies
of each card template per sheet). WeasyPrint does not execute JavaScript,
so this script expands the <template> elements the same way before
rendering.

Usage:  python3 scripts/render_kneeboard.py [output.pdf]
Requires: pip install weasyprint
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "kneeboard" / "kneeboard.html"

FRONT_NOTE = ("Yak-52 fuel test kneeboard — print 100% scale, no margins, "
              "duplex FLIP ON SHORT EDGE, cut on dashed lines (cards 99 × 173 mm)")
BACK_NOTE = 'Back side — aligns behind the fronts with duplex "flip on short edge"'


def sheet(card_html: str, note: str) -> str:
    cuts = (
        '<div class="cut-v" style="left:99mm"></div>'
        '<div class="cut-v" style="left:198mm"></div>'
        '<div class="cut-h" style="top:18.5mm"></div>'
        '<div class="cut-h" style="top:191.5mm"></div>'
        f'<div class="waste-note" style="top:6mm">{note}</div>'
    )
    cards = "".join(
        f'<div class="card c{i}">{card_html}</div>' for i in range(3)
    )
    return f'<div class="sheet">{cuts}{cards}</div>'


def expand(src: str) -> str:
    templates = dict(
        re.findall(r'<template id="(\w+)">(.*?)</template>', src, re.S)
    )
    body = sheet(templates["front"], FRONT_NOTE) + sheet(templates["back"], BACK_NOTE)
    src = re.sub(r"<template.*</template>", body, src, flags=re.S)
    src = re.sub(r"<script>.*</script>", "", src, flags=re.S)
    return src


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "kneeboard" / "kneeboard.pdf"
    from weasyprint import HTML as WHTML
    WHTML(string=expand(HTML.read_text()), base_url=str(HTML.parent)).write_pdf(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
