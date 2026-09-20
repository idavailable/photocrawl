#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_report.py —— 生成「图集总览」HTML，可直接预览、点开看图。

用法：
  python tools/make_report.py --root beauty1000 --thumbs 8
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".jfif"}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1b1f24;--dim:#6b7280;--line:#e5e7eb;--accent:#d9483b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{background:linear-gradient(135deg,#fff 0%,#f0f2f5 100%);border-bottom:1px solid var(--line);
 padding:26px 32px;position:sticky;top:0;z-index:9}
h1{margin:0 0 6px;font-size:22px;letter-spacing:.5px}
.sub{color:var(--dim);font-size:13px}
.stats{display:flex;gap:26px;margin-top:16px;flex-wrap:wrap}
.stat b{display:block;font-size:22px;color:var(--accent);line-height:1.2}
.stat span{font-size:12px;color:var(--dim)}
.wrap{padding:22px 32px 80px;max-width:1500px;margin:0 auto}
.site{margin-bottom:34px}
.site h2{font-size:17px;margin:0 0 4px;padding-left:10px;border-left:4px solid var(--accent)}
.site .meta{color:var(--dim);font-size:12px;margin:0 0 14px;padding-left:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;
 transition:.18s;display:flex;flex-direction:column}
.card:hover{box-shadow:0 6px 22px rgba(0,0,0,.10);transform:translateY(-2px)}
.t{display:flex;justify-content:space-between;gap:10px;padding:11px 13px 7px;align-items:flex-start}
.t .name{font-size:13.5px;font-weight:600;line-height:1.35;word-break:break-word}
.t .n{flex:0 0 auto;font-size:11px;background:#fdeceb;color:var(--accent);
 border-radius:20px;padding:2px 9px;white-space:nowrap;margin-top:2px}
.ths{display:grid;grid-template-columns:repeat(4,1fr);gap:3px;padding:0 3px 3px}
.ths img{width:100%;aspect-ratio:3/4;object-fit:cover;background:#eceff3;border-radius:3px;display:block}
.thb{padding:7px 13px 12px;color:var(--dim);font-size:11.5px;display:flex;justify-content:space-between;gap:8px}
.thb a{color:var(--dim);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.thb a:hover{color:var(--accent)}
footer{padding:20px 32px 50px;color:var(--dim);font-size:12px;text-align:center}
"""


def scan(root: Path, thumbs: int) -> list[dict]:
    sites = []
    for sd in sorted(p for p in root.iterdir() if p.is_dir()):
        galleries = []
        for album in sorted(p for p in sd.iterdir() if p.is_dir()):
            files = sorted(f for f in album.iterdir()
                           if f.is_file() and f.suffix.lower() in IMG_EXT and not f.name.startswith("_"))
            if not files:
                continue
            size = sum(f.stat().st_size for f in files)
            src = ""
            mp = album / "_meta.json"
            if mp.exists():
                try:
                    src = json.loads(mp.read_text(encoding="utf-8")).get("source", "")
                except Exception:
                    pass
            galleries.append({"title": album.name, "files": files, "count": len(files),
                              "mb": size / 1048576, "source": src,
                              "rels": [f"{sd.name}/{album.name}/{f.name}" for f in files[:thumbs]]})
        if galleries:
            sites.append({"site": sd.name, "galleries": galleries,
                          "images": sum(g["count"] for g in galleries),
                          "mb": sum(g["mb"] for g in galleries)})
    sites.sort(key=lambda s: -s["images"])
    return sites


def build(root: Path, out_html: Path, thumbs: int) -> int:
    sites = scan(root, thumbs)
    total_imgs = sum(s["images"] for s in sites)
    total_mb = sum(s["mb"] for s in sites)
    total_albums = sum(len(s["galleries"]) for s in sites)

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>图集总览</title><style>", CSS, "</style></head><body>",
        "<header><h1>📸 写真图集总览</h1>",
        f"<div class='sub'>{html.escape(str(root.resolve()))}</div>",
        "<div class='stats'>",
        f"<div class='stat'><b>{total_imgs}</b><span>张图片</span></div>",
        f"<div class='stat'><b>{total_albums}</b><span>个图集</span></div>",
        f"<div class='stat'><b>{len(sites)}</b><span>个站点</span></div>",
        f"<div class='stat'><b>{total_mb:.0f}</b><span>MB</span></div>",
        "</div></header><div class='wrap'>",
    ]

    for s in sites:
        parts.append("<section class='site'>")
        parts.append(f"<h2>{html.escape(s['site'])}</h2>")
        parts.append(f"<p class='meta'>{len(s['galleries'])} 个图集 · {s['images']} 张 · {s['mb']:.0f} MB</p>")
        parts.append("<div class='grid'>")
        for g in s["galleries"]:
            parts.append("<article class='card'>")
            parts.append("<div class='t'><div class='name'>" + html.escape(g["title"]) + "</div>"
                         f"<div class='n'>{g['count']}</div></div>")
            parts.append("<div class='ths'>")
            for r in g["rels"]:
                parts.append(f"<img loading='lazy' src='{html.escape(r)}' alt=''>")
            parts.append("</div>")
            src = g["source"]
            link = (f"<a href='{html.escape(src)}' target='_blank' rel='noreferrer'>{html.escape(src)}</a>"
                    if src else "<span></span>")
            parts.append(f"<div class='thb'>{link}<span>{g['mb']:.1f} MB</span></div>")
            parts.append("</article>")
        parts.append("</div></section>")

    parts.append("</div><footer>由 photocrawl 生成 · 图片版权归原作者所有，仅供个人收藏</footer></body></html>")
    out_html.write_text("".join(parts), encoding="utf-8")
    print(f"✅ 已生成 {out_html}（{total_imgs} 张 / {total_albums} 图集 / {total_mb:.0f} MB）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="beauty1000")
    ap.add_argument("--out", default="")
    ap.add_argument("--thumbs", type=int, default=8)
    a = ap.parse_args()
    root = Path(a.root)
    out = Path(a.out) if a.out else root / "图集总览.html"
    return build(root, out, a.thumbs)


if __name__ == "__main__":
    sys.exit(main())
