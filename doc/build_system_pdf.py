from __future__ import annotations

"""Build doc/HH_Agent_System_Documentation.pdf from its Markdown source.

Optional documentation-only dependencies:
    python -m pip install mistune weasyprint

The generated layout intentionally reuses the visual language of HH Agent
Observatory. No network access is required.
"""

import html
import os
import re
from pathlib import Path

import mistune
from weasyprint import HTML


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE = HERE / "HH_Agent_System_Documentation.md"
OUTPUT = HERE / "HH_Agent_System_Documentation.pdf"


class DocRenderer(mistune.HTMLRenderer):
    def __init__(self) -> None:
        super().__init__(escape=False)
        self._slug_counts: dict[str, int] = {}

    @staticmethod
    def _plain(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text)

    def _slug(self, text: str) -> str:
        plain = html.unescape(self._plain(text)).lower()
        plain = re.sub(r"[^0-9a-zа-яё]+", "-", plain, flags=re.IGNORECASE).strip("-")
        base = plain or "section"
        count = self._slug_counts.get(base, 0)
        self._slug_counts[base] = count + 1
        return base if count == 0 else f"{base}-{count + 1}"

    def heading(self, text: str, level: int, **attrs) -> str:
        slug = self._slug(text)
        return f'<h{level} id="{slug}">{text}</h{level}>\n'

    def block_code(self, code: str, info: str | None = None) -> str:
        lang = (info or "").strip().lower()
        if lang == "mermaid":
            return """
            <div class="arch">
              <div class="arch-sources">
                <span class="s-hh">HH.ru</span><span class="s-ya">Yandex Jobs</span><span class="s-vk">VK Team</span><span class="s-tb">Т-Банк</span>
              </div>
              <div class="arch-arrow">↓</div>
              <div class="arch-core"><b>SQLite</b><small>vacancies · evaluations · applications</small></div>
              <div class="arch-arrow">↓</div>
              <div class="arch-eval">
                <div><b>FILTER</b><small>hard filters + appeal</small></div>
                <i>→</i><div><b>SCORE</b><small>Ollama + evidence</small></div>
                <i>→</i><div><b>RESUME</b><small>match + cover letter</small></div>
              </div>
              <div class="arch-branches">
                <div><b>Telegram</b><small>review / approval</small></div>
                <div><b>HH / Yandex / VK</b><small>source-specific apply</small></div>
                <div><b>Observatory</b><small>read only</small></div>
              </div>
            </div>
            """
        return super().block_code(code, info)


def parse_meta(md: str) -> dict[str, str]:
    values: dict[str, str] = {}
    mapping = {
        "Актуально": "date",
        "Снимок кода": "snapshot",
        "Платформа": "platform",
    }
    for label, key in mapping.items():
        match = re.search(rf"\*\*{re.escape(label)}:\*\*\s*(.+?)\s*(?:  )?$", md, re.MULTILINE)
        if match:
            values[key] = match.group(1).replace("`", "").strip()
    return values


def build_toc(md: str) -> str:
    items: list[tuple[int, str, str]] = []
    counts: dict[str, int] = {}
    for line in md.splitlines():
        match = re.match(r"^(##)\s+(.+)$", line)
        if not match:
            continue
        level = len(match.group(1))
        raw = re.sub(r"[`*_]", "", match.group(2)).strip()
        slug = re.sub(r"[^0-9a-zа-яё]+", "-", raw.lower(), flags=re.IGNORECASE).strip("-") or "section"
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        if n:
            slug = f"{slug}-{n + 1}"
        items.append((level, raw, slug))
    links = []
    for level, title, slug in items:
        cls = ""
        links.append(f'<a class="{cls}" href="#{html.escape(slug)}"><span>{html.escape(title)}</span><i></i></a>')
    return "\n".join(links)


def font_css() -> str:
    override = os.getenv("HH_DOC_FONT_DIR")
    if override:
        d = Path(override)
        candidates = [
            (d / "Inter-Regular.otf", 400),
            (d / "Inter-SemiBold.otf", 600),
            (d / "Inter-Bold.otf", 700),
        ]
        entries = []
        for path, weight in candidates:
            if path.exists():
                entries.append(
                    "@font-face{font-family:'HHDoc';font-style:normal;"
                    f"font-weight:{weight};src:url('{path.as_uri()}');}}"
                )
        if entries:
            return "\n".join(entries)

    cyr = ROOT / "dashboard" / "static" / "fonts" / "manrope-cyrillic.woff2"
    lat = ROOT / "dashboard" / "static" / "fonts" / "manrope-latin.woff2"
    if cyr.exists() and lat.exists():
        return f"""
        @font-face{{font-family:'HHDoc';font-style:normal;font-weight:300 800;src:url('{cyr.as_uri()}') format('woff2');}}
        @font-face{{font-family:'HHDoc';font-style:normal;font-weight:300 800;src:url('{lat.as_uri()}') format('woff2');}}
        """
    return ""


def build_cover(meta: dict[str, str]) -> str:
    snapshot = html.escape(meta.get("snapshot", "main"))
    date = html.escape(meta.get("date", ""))
    return f"""
    <section class="cover">
      <div class="cover-topline"><b>hh-agent</b><span>.SYSTEM DOSSIER</span></div>
      <div class="cover-kicker">LOCAL-FIRST AUTOMATION / PRODUCTION ARCHITECTURE</div>
      <h1>Системная<br><em>документация</em></h1>
      <p class="cover-lead">Архитектура, LLM-оценка, контролируемые отклики, наблюдаемость и безопасная доставка в production.</p>

      <div class="pipeline">
        <div><i>01</i><b>COLLECT</b><span>сбор</span></div>
        <strong>→</strong>
        <div><i>02</i><b>FILTER</b><span>отсев</span></div>
        <strong>→</strong>
        <div><i>03</i><b>SCORE</b><span>LLM</span></div>
        <strong>→</strong>
        <div><i>04</i><b>REVIEW</b><span>человек</span></div>
        <strong>→</strong>
        <div><i>05</i><b>APPLY</b><span>отклик</span></div>
      </div>

      <div class="source-strip"><span class="hh">hh / HeadHunter</span><span class="tb">T / Т-Банк</span><span class="ya">Я / Яндекс</span><span class="vk">VK / VK Team</span></div>

      <div class="cover-cards">
        <div><small>CODE SNAPSHOT</small><b>{snapshot}</b></div>
        <div><small>UPDATED</small><b>{date}</b></div>
        <div><small>CONTROL</small><b>explicit approval</b></div>
        <div><small>OBSERVABILITY</small><b>read only</b></div>
      </div>

      <div class="cover-foot"><span>HH AGENT / rudenko.one</span><span>Windows · Python · Playwright · Ollama · Telegram · FastAPI · Octopus</span></div>
    </section>
    """


def css() -> str:
    return f"""
    {font_css()}
    :root {{
      --bg:#08090a; --surface:#121315; --surface2:#18191c; --line:#2b2d30;
      --text:#e1e2e3; --muted:#999ca0; --violet:#9448ff; --lilac:#b58aff;
      --pink:#ff449a; --amber:#e8b265; --green:#90d0ac; --red:#f18d97;
    }}
    @page {{
      size:A4; margin:16mm 15mm 17mm 15mm; background:var(--bg);
      @bottom-left {{ content:"HH AGENT · SYSTEM DOSSIER"; color:#676b70; font:500 7.5pt HHDoc, sans-serif; letter-spacing:.08em; }}
      @bottom-right {{ content:counter(page); color:#8b8f94; font:600 8pt HHDoc, sans-serif; }}
    }}
    @page:first {{ margin:0; @bottom-left{{content:none}} @bottom-right{{content:none}} }}
    html, body {{ margin:0; padding:0; color:var(--text); background:var(--bg); font-family:HHDoc,'Segoe UI',Arial,sans-serif; font-size:9.6pt; line-height:1.58; }}
    .cover {{ min-height:297mm; box-sizing:border-box; padding:18mm 18mm 15mm; position:relative; overflow:hidden; background:
      radial-gradient(circle at 83% 16%, rgba(148,72,255,.17), transparent 32%),
      radial-gradient(circle at 14% 70%, rgba(255,68,154,.10), transparent 35%), #08090a; page-break-after:always; }}
    .cover:before {{ content:""; position:absolute; width:170mm; height:170mm; border:1px solid rgba(148,72,255,.12); border-radius:50%; right:-82mm; top:-72mm; box-shadow:0 0 0 22mm rgba(148,72,255,.018),0 0 0 45mm rgba(255,68,154,.012); }}
    .cover-topline {{ display:flex; align-items:baseline; gap:10px; border-bottom:1px solid #25272a; padding-bottom:8mm; }}
    .cover-topline b {{ font-size:23pt; letter-spacing:-.06em; }} .cover-topline span {{ color:#8a8e93; font-size:8pt; letter-spacing:.28em; }}
    .cover-kicker {{ color:#c28bff; font-size:8pt; font-weight:700; letter-spacing:.15em; margin-top:18mm; }}
    .cover h1 {{ font-size:40pt; line-height:.98; letter-spacing:-.055em; margin:5mm 0 6mm; font-weight:700; }} .cover h1 em {{ color:#ff6bb1; font-style:normal; }}
    .cover-lead {{ color:#b4b8bd; font-size:13pt; line-height:1.5; max-width:150mm; margin:0 0 13mm; }}
    .pipeline {{ display:flex; align-items:center; gap:3mm; padding:7mm; border:1px solid #3b1e45; border-radius:13px; background:#0b0a0f; }}
    .pipeline div {{ flex:1; text-align:center; min-width:0; }} .pipeline i {{ display:block; width:12mm; height:12mm; line-height:12mm; margin:0 auto 2mm; border:1px solid #8b42c7; border-radius:50%; color:#ca94ff; font-style:normal; font-size:7.5pt; background:#17131d; }}
    .pipeline div:nth-of-type(4) i,.pipeline div:nth-of-type(5) i {{ border-color:#b52a86; color:#ff76b9; background:#1a111b; }}
    .pipeline b {{ display:block; font-size:8pt; letter-spacing:.04em; }} .pipeline span {{ display:block; color:#777b80; font-size:7pt; margin-top:1mm; }} .pipeline strong {{ color:#5c3a70; font-size:11pt; }}
    .source-strip {{ display:flex; gap:3mm; margin:5mm 0 12mm; }} .source-strip span {{ border-radius:8px; padding:2.5mm 4mm; background:#151619; border:1px solid #2b2d30; font-size:7.5pt; color:#b9bdc2; }}
    .source-strip .hh{{color:#ff8096;border-color:#4f2931}} .source-strip .tb{{color:#eac46e;border-color:#4c4125}} .source-strip .ya{{color:#ef9778;border-color:#542e25}} .source-strip .vk{{color:#bd91ff;border-color:#462b61}}
    .cover-cards {{ display:grid; grid-template-columns:1fr 1fr; gap:4mm; }} .cover-cards div {{ border:1px solid var(--line); background:var(--surface); border-radius:12px; padding:5mm; }} .cover-cards small {{ display:block; color:#7f8388; font-size:6.6pt; letter-spacing:.12em; margin-bottom:1mm; }} .cover-cards b {{ font-size:10pt; font-weight:600; }}
    .cover-foot {{ margin-top:9mm; display:flex; justify-content:space-between; gap:8mm; border-top:1px solid #25272a; padding-top:5mm; color:#777b80; font-size:7pt; }}

    .toc {{ page-break-after:always; padding-top:3mm; }} .toc .eyebrow,.eyebrow {{ color:#bf85ff; font-size:7pt; letter-spacing:.15em; font-weight:700; }} .toc h2 {{ margin-top:2mm; }} .toc-grid {{ columns:2; column-gap:12mm; margin-top:7mm; }} .toc a {{ break-inside:avoid; text-decoration:none; color:#cdd0d4; display:flex; gap:2mm; align-items:baseline; padding:2.2mm 0; border-bottom:1px solid #1e2023; font-size:8.6pt; }} .toc a i {{ flex:1; border-bottom:1px dotted #3c3f43; }} .toc a.toc-sub {{ color:#8f9499; padding-left:4mm; font-size:7.7pt; }}

    main > h1:first-child, main > p.meta-line {{ display:none; }}
    h1,h2,h3,h4 {{ color:#dfe1e4; line-height:1.18; page-break-after:avoid; }} h2 {{ font-size:20pt; letter-spacing:-.035em; margin:7mm 0 4mm; padding-top:2mm; }} h2:before {{ content:""; display:block; width:12mm; height:1.1mm; border-radius:2px; background:linear-gradient(90deg,var(--violet),var(--pink)); margin-bottom:3mm; }} h3 {{ font-size:12.5pt; margin:6mm 0 2mm; color:#d6d9dc; }} h4 {{ font-size:10pt; margin:4mm 0 1.5mm; }}
    p {{ margin:0 0 3mm; color:#c4c7cb; }} strong {{ color:#eceef0; }} em {{ color:#d9b8ff; }} a {{ color:#c391ff; }}
    blockquote {{ margin:5mm 0; padding:4mm 5mm; border-left:2px solid var(--violet); background:#131117; color:#c8c1d2; border-radius:0 8px 8px 0; break-inside:avoid; }} blockquote p {{ margin:0; }}
    hr {{ border:0; height:1px; background:#242629; margin:7mm 0; }}
    ul,ol {{ margin:2mm 0 4mm; padding-left:6mm; color:#c3c6ca; }} li {{ margin:1.2mm 0; }} li::marker {{ color:#a66af0; }}
    code {{ font-family:'Cascadia Mono','Consolas',monospace; color:#ddb9ff; background:#17131d; border:1px solid #34253e; border-radius:4px; padding:.25mm 1mm; font-size:8.1pt; }}
    pre {{ white-space:pre-wrap; overflow-wrap:anywhere; margin:4mm 0 5mm; padding:4.5mm; background:#0f1012; border:1px solid #292b2f; border-radius:10px; color:#b9bec4; font:7.6pt/1.5 'Cascadia Mono','Consolas',monospace; break-inside:avoid; }} pre code {{ border:0; padding:0; background:transparent; color:inherit; }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; margin:4mm 0 6mm; font-size:8.1pt; break-inside:avoid; border:1px solid #2b2d31; border-radius:10px; overflow:hidden; }} thead th {{ background:#1a151f; color:#d7b5ff; font-weight:650; text-align:left; }} th,td {{ padding:2.6mm 3mm; border-bottom:1px solid #292b2e; vertical-align:top; }} tbody tr:nth-child(even) td {{ background:#101113; }} tbody tr:nth-child(odd) td {{ background:#131416; }} tbody tr:last-child td {{ border-bottom:0; }}
    h2 + p, h3 + p {{ color:#c8cbcf; }}
    .doc-content {{ padding-bottom:3mm; }}
    .doc-content > p:first-of-type {{ color:#a9adb2; }}
    .doc-content h2:nth-of-type(1) {{ margin-top:1mm; }}
    .snapshot-band {{ display:grid; grid-template-columns:repeat(4,1fr); gap:3mm; margin:3mm 0 7mm; page-break-inside:avoid; }} .snapshot-band div {{ padding:4mm; border-radius:10px; border:1px solid #2b2d31; background:#121315; }} .snapshot-band small {{ display:block; font-size:6.5pt; color:#898e94; letter-spacing:.08em; }} .snapshot-band b {{ display:block; font-size:9pt; margin-top:1mm; }}
    .arch {{ margin:4mm 0 6mm; padding:5mm; border:1px solid #392442; border-radius:13px; background:radial-gradient(circle at 50% 42%, rgba(148,72,255,.08), transparent 48%), #0b0a0f; break-inside:avoid; }}
    .arch-sources {{ display:grid; grid-template-columns:repeat(4,1fr); gap:3mm; }} .arch-sources span {{ text-align:center; padding:3mm 2mm; background:#151619; border:1px solid #2b2d30; border-radius:8px; font-size:8pt; font-weight:600; }} .arch-sources .s-hh{{color:#ff8096;border-color:#4f2931}} .arch-sources .s-ya{{color:#ef9778;border-color:#542e25}} .arch-sources .s-vk{{color:#bd91ff;border-color:#462b61}} .arch-sources .s-tb{{color:#eac46e;border-color:#4c4125}}
    .arch-arrow {{ text-align:center; color:#8c54b1; padding:1.5mm 0; font-size:11pt; }}
    .arch-core {{ text-align:center; border:1px solid #5d2e80; background:#1a1222; border-radius:9px; padding:3.5mm; }} .arch-core b{{display:block;color:#ddbaff;font-size:11pt}} .arch-core small{{color:#92969b}}
    .arch-eval {{ display:flex; align-items:center; gap:2mm; margin-top:3mm; }} .arch-eval div {{ flex:1; text-align:center; padding:4mm 2mm; border:1px solid #3b2a46; background:#121016; border-radius:9px; }} .arch-eval b{{display:block;font-size:8pt;color:#e3e5e7}} .arch-eval small{{display:block;color:#8f949a;font-size:7pt;margin-top:1mm}} .arch-eval i{{font-style:normal;color:#7b438f}}
    .arch-branches {{ display:grid; grid-template-columns:repeat(3,1fr); gap:3mm; margin-top:4mm; }} .arch-branches div {{ padding:3.5mm; border:1px solid #2b2d31; background:#131416; border-radius:9px; text-align:center; }} .arch-branches b{{display:block;font-size:8.3pt}} .arch-branches small{{display:block;color:#8f949a;font-size:7pt;margin-top:1mm}}
    """


def main() -> int:
    md = SOURCE.read_text(encoding="utf-8")
    meta = parse_meta(md)
    content_md = md.split("\n---\n", 1)[1] if "\n---\n" in md else md
    renderer = DocRenderer()
    markdown = mistune.create_markdown(renderer=renderer, plugins=["table", "strikethrough"])
    body = markdown(content_md)
    toc = build_toc(md)

    # Give the first page after the TOC a small project snapshot row.
    snapshot = f"""
      <div class="snapshot-band">
        <div><small>RUNTIME</small><b>Windows-first</b></div>
        <div><small>LLM</small><b>local Ollama</b></div>
        <div><small>DECISION</small><b>explicit approval</b></div>
        <div><small>DEPLOY</small><b>PR → Octopus</b></div>
      </div>
    """

    document = f"""<!doctype html>
    <html lang="ru"><head><meta charset="utf-8"><title>HH Agent — системная документация</title><style>{css()}</style></head>
    <body>
      {build_cover(meta)}
      <section class="toc"><div class="eyebrow">CONTENTS / 2026</div><h2>Содержание</h2><div class="toc-grid">{toc}</div></section>
      <main class="doc-content">{snapshot}{body}</main>
    </body></html>"""

    HTML(string=document, base_url=str(ROOT)).write_pdf(
        OUTPUT,
        presentational_hints=True,
        pdf_identifier="HH-Agent-System-Documentation-2026-09-14",
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
