"""A local review page: small thumbnails of what would go to the Trash next to what stays.

Thumbnails (about 360 px, JPEG) live in Spacewise's own data folder, never next to the photos.
The page is self-contained: no fonts, scripts or images from the internet. Videos are listed
with their size (Spacewise does not decode video).
"""

from __future__ import annotations

import html
from pathlib import Path

from . import scan

THUMB = 360


def _thumb(src: Path, out: Path) -> str:
    import hashlib
    dest = out / (hashlib.sha1(str(src).encode()).hexdigest()[:16] + ".jpg")
    if not dest.exists():
        im = scan.open_photo(src)
        if im is None:
            return ""
        try:
            from PIL import ImageOps
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass
        im = im.convert("RGB"); im.thumbnail((THUMB, THUMB)); im.save(dest, "JPEG", quality=80)
    return dest.name


def gb(n: float) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{max(1, round(n / 1024))} KB" if n else "0 KB"


CSS = """
:root{--bg:#e9ecea;--panel:#fbfcfb;--ink:#1c2321;--soft:#5b6662;--keep:#2f6b4f;--go:#a8640a;--line:#cfd6d2;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#161a19;--panel:#1f2523;--ink:#e6ebe8;--soft:#9aa6a1;--keep:#6fbf96;--go:#e0a24a;--line:#2e3633;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,system-ui,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:30px;line-height:1.2;margin:0 0 6px;font-weight:650}
.lede{color:var(--soft);margin:0 0 28px;max-width:70ch}
.sum{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:36px}
.sum div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:180px}
.sum b{display:block;font-size:22px}
section{margin-top:36px}h2{font-size:20px;margin:0 0 4px}section>p{color:var(--soft);margin:0 0 14px;max-width:75ch}
.group{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px;display:flex;flex-wrap:wrap;gap:12px}
figure{margin:0;width:180px}figure img{width:180px;height:135px;object-fit:cover;border-radius:6px;display:block;background:var(--line)}
.tile{width:180px;height:135px;border-radius:6px;background:var(--line);display:flex;align-items:center;justify-content:center;font-weight:600;color:var(--soft)}
figure.keep img,figure.keep .tile{outline:3px solid var(--keep);outline-offset:2px}figure.go img,figure.go .tile{outline:2px dashed var(--go);outline-offset:2px}
figcaption{font-size:13px;margin-top:6px;word-break:break-all}figcaption .n{white-space:nowrap}figcaption em{font-style:normal;font-weight:600}
figure.keep em{color:var(--keep)}figure.go em{color:var(--go)}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
td{padding:9px 14px;border-top:1px solid var(--line);font-size:14px;word-break:break-all}tr:first-child td{border-top:0}td.n{text-align:right;white-space:nowrap}
.note{font-size:14px;color:var(--soft)}
"""


def _fig(f: dict, out: Path, keep: bool, note: str) -> str:
    name = html.escape(Path(f["path"]).name)
    t = _thumb(Path(f["path"]), out) if f["kind"] in ("photo",) else ""
    img = (f'<img src="{t}" alt="{name}">' if t else
           f'<div class="tile">{html.escape(Path(f["path"]).suffix.lstrip(".").upper() or "FILE")}</div>')
    cls, label = ("keep", "Stays") if keep else ("go", "To the Trash if you agree")
    return f'<figure class="{cls}">{img}<figcaption><em>{label}</em><br>{name} · <span class="n">{gb(f["size"])}</span><br>{html.escape(note)}</figcaption></figure>'


def build(plan: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    s = plan["summary"]; parts = []
    parts.append(f"<h1>Spacewise review: {html.escape(plan['folder_display'])}</h1>"
                 "<p class='lede'>Nothing has been moved. Green outlines stay where they are; dashed amber ones go to the "
                 "Trash only if you agree in Hermes, and can be put back with Spacewise undo. Grey items are listed "
                 "for you to decide yourself: Spacewise never moves them.</p>")
    parts.append("<div class='sum'>" + "".join(
        f"<div>{label}<b>{gb(s[key]['bytes'])}</b>{s[key]['files']} file{'' if s[key]['files'] == 1 else 's'}</div>"
        for key, label in (("green", "Safe to free"), ("amber", "Check first"), ("grey", "For you to decide"))) + "</div>")
    groups = plan["groups"]
    if groups["exact_duplicates"]:
        parts.append("<section><h2>Exact duplicates</h2><p>Byte-for-byte identical copies. One copy always stays.</p>")
        for g in groups["exact_duplicates"][:200]:
            parts.append("<div class='group'>" + _fig(g["keep"], out, True, "the copy that stays") + "".join(
                _fig(x, out, False, "identical copy") for x in g["extra"]) + "</div>")
        parts.append("</section>")
    if groups["installers"]:
        parts.append("<section><h2>Leftover installers</h2><p>Disk images and packages. Make sure the app is installed before agreeing.</p><table>"
                     + "".join(f"<tr><td>{html.escape(Path(f['path']).name)}</td><td class='n'>{gb(f['size'])}</td></tr>" for f in groups["installers"])
                     + "</table></section>")
    if groups["similar_photos"]:
        parts.append("<section><h2>Similar photos</h2><p>Frames shot within seconds that look alike. The sharpest one stays. Look before you agree: Spacewise cannot see eyes or expressions.</p>")
        for g in groups["similar_photos"][:150]:
            parts.append("<div class='group'>" + _fig(g["keep"], out, True, "sharpest of the group") + "".join(
                _fig(x, out, False, "similar, less sharp") for x in g["extra"]) + "</div>")
        parts.append("</section>")
    if groups["blurry_or_dark"]:
        parts.append("<section><h2>Blurry or near-black photos</h2><p>Nothing in focus, or almost entirely black. Check them: some may be intentional.</p><div class='group'>"
                     + "".join(_fig(f, out, False, f.get("why", "")) for f in groups["blurry_or_dark"][:300]) + "</div></section>")
    grey = []
    if groups["large_videos"]:
        grey.append("<h2>Large videos</h2><p>Listed only. Spacewise never proposes moving a video because of its size.</p><table>"
                    + "".join(f"<tr><td>{html.escape(Path(f['path']).name)}</td><td class='n'>{gb(f['size'])}</td></tr>" for f in groups["large_videos"]) + "</table>")
    if groups["screenshots"]:
        grey.append(f"<h2>Screenshots</h2><p>{len(groups['screenshots'])} screenshots, {gb(sum(f['size'] for f in groups['screenshots']))}. Listed only.</p>")
    if groups["large_files"]:
        grey.append("<h2>Other large files</h2><p>Design files, archives and other large files. Spacewise does not judge them and has no way to move them. This list stays on your computer.</p><table>"
                    + "".join(f"<tr><td>{html.escape(f['path'])}</td><td class='n'>{gb(f['size'])}</td></tr>" for f in groups["large_files"]) + "</table>")
    if grey:
        parts.append("<section>" + "".join(grey) + "</section>")
    if plan["skipped"]:
        parts.append("<section><h2>Not looked at</h2><table>" + "".join(
            f"<tr><td>{html.escape(k)}</td><td class='n'>{v[0]}</td></tr>" for k, v in plan["skipped"].items()) + "</table></section>")
    page = out / "index.html"
    page.write_text("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                    f"<title>Spacewise review</title><style>{CSS}</style></head><body><main>{''.join(parts)}</main></body></html>",
                    encoding="utf-8")
    return page
