"""Replace literal non-ASCII glyphs in web/index.html with escapes.

The page is published without a charset meta of its own (the artifact wrapper
supplies the head), and a literal euro sign renders as mojibake wherever the
host serves the file as latin-1. A JS \\u escape and an HTML entity both survive
that, so the page reads correctly regardless of how it is served.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "web" / "index.html"

# JS string literals -> \u escapes; HTML text -> entities.
JS_SUBS = {
    "€": "\\u20AC",   # EUR
}
HTML_SUBS = {
    "€": "&euro;",
    "…": "...",
}


def main() -> int:
    text = SRC.read_text(encoding="utf-8")
    # Everything from <script type="module"> onward is JS; before that is HTML.
    split = text.index('<script type="module">')
    html, js = text[:split], text[split:]
    for ch, rep in HTML_SUBS.items():
        html = html.replace(ch, rep)
    for ch, rep in JS_SUBS.items():
        js = js.replace(ch, rep)
    out = html + js
    SRC.write_text(out, encoding="utf-8")
    left = sorted({c for c in out if ord(c) > 127})
    print(f"rewrote {SRC.name}; non-ascii remaining: {left or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
