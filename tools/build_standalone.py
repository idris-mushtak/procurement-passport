"""Bundle the app into one HTML file that needs no network and no login.

The published artifact is private: opening its URL on a machine that is not
signed into the author's Claude account returns 404. For a pitch on someone
else's computer that is a total failure, so this build inlines the engine and
every data file into a single document that runs from a USB stick, an email
attachment, or a file:// path with the wifi off.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OUT = ROOT / "out" / "procurement-passport-standalone.html"

DATA_FILES = ["data.json", "provenance.json", "replay.json"]


def main() -> int:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    rules = (WEB / "rules.js").read_text(encoding="utf-8")

    # 1. Inline the data as a lookup the fetch shim reads from.
    blobs = {name: json.loads((WEB / name).read_text(encoding="utf-8"))
             for name in DATA_FILES if (WEB / name).exists()}
    inline = ("<script>window.__PP_FILES__ = "
              + json.dumps(blobs, separators=(",", ":"))
              + ";\n"
              # Keep the app's own `await fetch('./x.json')` calls working
              # untouched, so the online and offline builds stay identical.
              + """
(function () {
  const files = window.__PP_FILES__ || {};
  const real = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (input, init) {
    const url = String(input);
    const key = url.replace(/^\\.\\//, "").split("/").pop();
    if (Object.prototype.hasOwnProperty.call(files, key)) {
      return Promise.resolve(new Response(JSON.stringify(files[key]), {
        status: 200, headers: { "Content-Type": "application/json" },
      }));
    }
    return real ? real(input, init) : Promise.reject(new Error("offline"));
  };
})();
</script>""")

    # 2. Inline the engine, replacing the module import with the bundle itself.
    rules_inline = rules.replace("export {", "const __unused_exports = {")
    # deno's bundle ends with a single export block; turn it into locals.
    body = html.replace(
        '<script type="module">\nimport { evaluateTender, LADDER } from "./rules.js";',
        "<script type=\"module\">\n/* engine inlined from rules.js */\n"
        + rules_inline)

    if body == html:
        raise SystemExit("import line not found -- did index.html change?")

    out = body.replace("</style>", "</style>\n" + inline, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(out, encoding="utf-8")
    kb = OUT.stat().st_size // 1024
    print(f"wrote {OUT.relative_to(ROOT)}  ({kb} KB, self-contained)")
    if kb > 4000:
        print("  note: large for an email attachment; a USB stick is safer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
