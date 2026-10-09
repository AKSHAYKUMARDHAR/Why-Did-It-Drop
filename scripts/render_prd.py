"""Render docs/PRD.md as a standalone, theme-aware HTML page (docs/PRD.html).

    python -m scripts.render_prd
"""
import pathlib
import re

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC, OUT = ROOT / "docs" / "PRD.md", ROOT / "docs" / "PRD.html"

CSS = """
/* Layout: one reading column, styled like the insurer's own summary sheet: a schedule header, ruled tables, clause-style labels. */
:root {
  --paper: #f5f7f6; --sheet: #ffffff; --ink: #14212b; --muted: #56646e; --rule: #d6dde0;
  --accent: #0d6b66; --accent-soft: #e3efed; --warn: #9a520c; --warn-soft: #f8ecdf;
  --display: "Newsreader", Georgia, "Times New Roman", serif;
  --body: "Public Sans", "Segoe UI", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #0e1417; --sheet: #131b1f; --ink: #e4eaed; --muted: #9babb4; --rule: #27343b;
  --accent: #5cbcb3; --accent-soft: #15292a; --warn: #e0a05e; --warn-soft: #2a2016; color-scheme: dark; } }
:root[data-theme="dark"] {
  --paper: #0e1417; --sheet: #131b1f; --ink: #e4eaed; --muted: #9babb4; --rule: #27343b;
  --accent: #5cbcb3; --accent-soft: #15292a; --warn: #e0a05e; --warn-soft: #2a2016; color-scheme: dark; }
* { box-sizing: border-box; }
body { background: var(--paper); color: var(--ink); font: 16px/1.65 var(--body); padding-inline: 16px; padding-block: 28px 64px; }
.sheet { max-width: 860px; margin: 0 auto; background: var(--sheet); border: 1px solid var(--rule); border-radius: 6px; padding-inline: clamp(18px, 5vw, 56px); padding-block: 36px 48px; }
.schedule { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0; border: 1px solid var(--rule); border-radius: 4px; margin-bottom: 28px; font: 12px/1.4 var(--mono); }
.schedule div { padding: 8px 12px; border-right: 1px solid var(--rule); min-width: 0; }
.schedule div:last-child { border-right: 0; }
.schedule span { display: block; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; font-size: 10.5px; }
h1 { font: 600 clamp(30px, 5.2vw, 44px)/1.12 var(--display); letter-spacing: -.01em; margin: 0 0 6px; text-wrap: balance; }
.sub { font: 400 clamp(17px, 2.4vw, 20px)/1.4 var(--display); color: var(--muted); margin: 0 0 4px; }
h2 { font: 600 26px/1.25 var(--display); margin: 44px 0 12px; padding-top: 18px; border-top: 1px solid var(--rule); text-wrap: balance; }
h3 { font: 600 17px/1.35 var(--body); margin: 26px 0 8px; }
p, li { max-width: 68ch; }
a { color: var(--accent); text-underline-offset: 2px; }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
strong { font-weight: 650; }
ul, ol { padding-left: 1.3em; }
li { margin: 4px 0; }
.table-wrap { overflow-x: auto; margin: 14px 0 18px; border: 1px solid var(--rule); border-radius: 4px; }
table { border-collapse: collapse; width: 100%; font-size: 14.5px; line-height: 1.45; }
th { text-align: left; font: 600 12px/1.3 var(--body); letter-spacing: .04em; text-transform: uppercase; color: var(--muted); background: var(--accent-soft); padding: 10px 12px; border-bottom: 1px solid var(--rule); }
td { padding: 9px 12px; border-bottom: 1px solid var(--rule); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
td { font-variant-numeric: tabular-nums; }
table.bill td:nth-child(n+2), table.bill th:nth-child(n+2) { text-align: right; white-space: nowrap; }
table.bill td:nth-child(3) { color: var(--accent); }
table.bill td:nth-child(4) { color: var(--warn); }
table.bill tr:last-child td { background: var(--warn-soft); }
ul.gate { list-style: none; padding-left: 0; }
ul.gate li { padding-left: 30px; position: relative; }
ul.gate li::before { content: ""; position: absolute; left: 0; top: .32em; width: 16px; height: 16px; border: 1.5px solid var(--muted); border-radius: 3px; }
.foot { margin-top: 40px; font-size: 13px; color: var(--muted); }
@media (max-width: 520px) { body { font-size: 15.5px; } .schedule div { border-right: 0; border-bottom: 1px solid var(--rule); } .schedule div:last-child { border-bottom: 0; } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; } }
"""


def render() -> str:
    md = SRC.read_text(encoding="utf-8")
    lines = md.splitlines()
    title = lines[0].removeprefix("# ").strip()
    byline = lines[2].strip()
    body_md = "\n".join(lines[3:])
    html = markdown.markdown(body_md, extensions=["tables", "sane_lists"])
    html = re.sub(r"<table>", '<div class="table-wrap"><table>', html).replace("</table>", "</table></div>")
    # The worked example is the one table whose header starts with "Bill item"
    html = html.replace('<div class="table-wrap"><table>\n<thead>\n<tr>\n<th>Bill item</th>',
                        '<div class="table-wrap"><table class="bill">\n<thead>\n<tr>\n<th>Bill item</th>')
    # Release-gate checklist
    html = re.sub(r"<ul>\n(<li>\[ \] .*?)</ul>", lambda m: '<ul class="gate">\n' + m.group(1).replace("[ ] ", "") + "</ul>", html, flags=re.S)
    html = re.sub(r'<a href="(http[^"]+)"', r'<a href="\1" target="_blank" rel="noopener"', html)
    name, _, subtitle = title.removeprefix("PRD: ").partition(" — ")
    author, date, status = [x.strip() for x in byline.split("·")]
    return f"""<title>Why Did It Drop? PRD</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,400;6..72,600&family=Public+Sans:wght@400;600;700&display=swap">
<style>{CSS}</style>
<main class="sheet">
  <div class="schedule" aria-label="Document details">
    <div><span>Document</span>Product requirements</div>
    <div><span>Author</span>{author}</div>
    <div><span>Date</span>{date}</div>
    <div><span>Status</span>{status.removeprefix("Status: ")}</div>
  </div>
  <h1>{name.strip('"')}</h1>
  <p class="sub">{subtitle}</p>
  {html}
  <p class="foot">Copy in the repository: docs/PRD.md. Figures are from the sources listed above, as of October 2026.</p>
</main>
"""


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT}")
