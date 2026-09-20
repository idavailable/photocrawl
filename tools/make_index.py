#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_index.py —— 把各站点抓取结果汇总成一份总目录，并统计张数/体积。

用法：
  python tools/make_index.py --root beauty1000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".jfif"}


def scan_site(site_dir: Path) -> dict:
    galleries = []
    total_imgs = 0
    total_bytes = 0
    for album in sorted(p for p in site_dir.iterdir() if p.is_dir()):
        files = [f for f in album.iterdir()
                 if f.is_file() and f.suffix.lower() in IMG_EXT and not f.name.startswith("_")]
        if not files:
            continue
        size = sum(f.stat().st_size for f in files)
        meta = {}
        mp = album / "_meta.json"
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        galleries.append({
            "title": meta.get("title") or album.name,
            "dir": album.name,
            "source": meta.get("source", ""),
            "count": len(files),
            "bytes": size,
        })
        total_imgs += len(files)
        total_bytes += size
    return {"site": site_dir.name, "galleries": galleries,
            "images": total_imgs, "bytes": total_bytes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="beauty1000")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"目录不存在：{root}")
        return 1

    sites = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        s = scan_site(d)
        if s["images"]:
            sites.append(s)

    grand_imgs = sum(s["images"] for s in sites)
    grand_bytes = sum(s["bytes"] for s in sites)

    out = {
        "root": str(root.resolve()),
        "total_images": grand_imgs,
        "total_mb": round(grand_bytes / 1048576, 1),
        "sites": [
            {
                "site": s["site"],
                "images": s["images"],
                "mb": round(s["bytes"] / 1048576, 1),
                "galleries": [
                    {k: g[k] for k in ("title", "dir", "count", "source")}
                    for g in s["galleries"]
                ],
            }
            for s in sites
        ],
    }
    (root / "index.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"📦 总计 {grand_imgs} 张，{grand_bytes/1048576:.1f} MB")
    print()
    print(f"{'站点':<12}{'图集':>6}{'图片':>8}{'体积':>10}")
    print("-" * 40)
    for s in sites:
        print(f"{s['site']:<12}{len(s['galleries']):>6}{s['images']:>8}{s['bytes']/1048576:>9.1f}M")
    print("-" * 40)
    print(f"{'合计':<12}{sum(len(s['galleries']) for s in sites):>6}{grand_imgs:>8}{grand_bytes/1048576:>9.1f}M")
    print(f"\n清单已写入 {root/'index.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
