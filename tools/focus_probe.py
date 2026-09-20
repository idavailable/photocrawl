#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""focus_probe.py —— 针对重点站的图集详情页做精细探测：真实图片清单 + 翻页方式。"""

from __future__ import annotations

import os
import re
import sys
import urllib.parse as up

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from bs4 import BeautifulSoup

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
PROXY = "http://127.0.0.1:7890"

TARGETS = [
    ("kkmzt",  "https://kkmzt.com/photo/169171", False),
    ("kkmzt2", "https://kkmzt.com/photo/189807", False),
    ("taotu",  "https://taotu.org/hot-girls/%e5%b0%8f%e8%94%a1%e5%a4%b4%e5%96%b5%e5%96%b5%e5%96%b5/00069-%e9%bb%91%e4%b8%9d%e8%be%85%e5%af%bc%e5%91%98-29p/", True),
    ("v2ph",   "https://www.v2ph.com/album/YTY-2152", True),
    ("yituyu", "https://www.yituyu.com/gallery/15793/", False),
    ("misskon", "https://misskon.com/117974-yeha-school-nurse-219-photos/", True),
    ("ilovexs", "https://ilovexs.com/post_id/2101280266195861505/", False),
    ("4kup",   "https://4kup.net/le-ledg-209b-gms/", False),
]

JUNK = re.compile(r"(logo|icon|avatar|sprite|placeholder|blank|spacer|pixel|advert|"
                  r"/ads?/|favicon|loading|btn|button|qr|wechat|alipay|paypal|"
                  r"histats|gif$|\.svg)", re.I)
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")


def main():
    sess = requests.Session()
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass

    for name, url, use_proxy in TARGETS:
        print("=" * 78)
        print(f"■ {name}  {url}")
        p = {"http": PROXY, "https": PROXY} if use_proxy else None
        try:
            r = sess.get(url, headers={"User-Agent": UA, "Referer": up.urlparse(url).scheme + "://" + up.urlparse(url).netloc + "/"},
                         timeout=30, proxies=p, verify=False)
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {e}")
            continue
        r.encoding = r.apparent_encoding or r.encoding
        soup = BeautifulSoup(r.text, "lxml")
        print(f"  HTTP {r.status_code}  {len(r.content)}B  title={soup.title.get_text(strip=True) if soup.title else ''}")

        # 全部 img 候选
        rows = []
        for im in soup.find_all("img"):
            v = ""
            used = ""
            for k in ("data-original", "data-src", "data-lazy-src", "data-echo",
                      "data-url", "data-image", "src"):
                if im.get(k):
                    v, used = im[k], k
                    break
            if not v:
                continue
            full = up.urljoin(url, v.strip())
            if JUNK.search(full):
                continue
            rows.append((full, used, " ".join(im.get("class", [])[:3]),
                         im.get("width", ""), im.get("height", "")))
        print(f"  过滤后图片候选 {len(rows)} 张（attr, 前 6 条）：")
        for u, used, cls, w, h in rows[:6]:
            print(f"    [{used}] {w}x{h} .{cls}  {u}")
        if len(rows) > 6:
            print(f"    … 其余 {len(rows)-6} 张，最后一张：{rows[-1][0]}")

        # 唯一图片 base 目录（判断是否同一图集目录）
        dirs = {}
        for u, *_ in rows:
            d = os.path.dirname(up.urlparse(u).path)
            dirs[d] = dirs.get(d, 0) + 1
        top = sorted(dirs.items(), key=lambda x: -x[1])[:4]
        print("  图片目录分布: " + " | ".join(f"{d}({n})" for d, n in top))

        # 翻页链接
        pags = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            t = a.get_text(" ", strip=True)
            if re.search(r"[?&]page=|/page/\d|page=\d|_p\d|/\d+\.html$", href) or \
               re.search(r"(下一页|下一頁|next|»)", t, re.I):
                full = up.urljoin(url, href)
                if full != url and full not in pags:
                    pags.append(full)
        print(f"  翻页候选 {len(pags)} 个：")
        for s in pags[:8]:
            print(f"    {s}")

        # 选择器
        from collections import Counter
        c = Counter()
        for im in soup.find_all("img"):
            par = im.parent
            if par is not None:
                cl = ".".join(par.get("class", [])[:2])
                c[f"{par.name}.{cl} img" if cl else f"{par.name} img"] += 1
            cl2 = ".".join(im.get("class", [])[:3])
            if cl2:
                c[f"img.{cl2}"] += 1
        print("  选择器: " + " | ".join(f"{k}({n})" for k, n in c.most_common(8)))
        print()


if __name__ == "__main__":
    main()
