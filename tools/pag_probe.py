#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pag_probe.py —— 探翻页规律 + 原图地址规律。"""

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


def sess():
    s = requests.Session()
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return s


def get(s, url, use_proxy, referer=""):
    p = {"http": PROXY, "https": PROXY} if use_proxy else None
    h = {"Referer": referer} if referer else {}
    return s.get(url, headers=h, timeout=30, proxies=p, verify=False, allow_redirects=True)


def count_imgs(html, url, sel_list):
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for sel in sel_list:
        out[sel] = len(soup.select(sel))
    return soup, out


def main():
    s = sess()

    # ---------- 1) v2ph 分页结构 ----------
    print("=" * 78)
    print("■ v2ph 分页结构")
    u = "https://www.v2ph.com/album/YTY-2152"
    r = get(s, u, True)
    soup = BeautifulSoup(r.text, "lxml")
    print(f"  page1 imgs={len(soup.select('img.img-fluid.album-photo'))}")
    navs = soup.select("nav, ul.pagination, div.pagination")
    for n in navs:
        txt = " ".join(n.get_text(" ", strip=True).split())[:200]
        print(f"  分页容器 <{n.name} class={n.get('class')}>: {txt}")
        for a in n.select("a[href]")[:12]:
            print(f"      a href={a.get('href')}  rel={a.get('rel')}  class={a.get('class')}  '{a.get_text(strip=True)[:12]}'")
    for pnum in (2, 3):
        r2 = get(s, f"{u}?page={pnum}", True)
        sp2 = BeautifulSoup(r2.text, "lxml")
        print(f"  ?page={pnum} → HTTP {r2.status_code}  imgs={len(sp2.select('img.img-fluid.album-photo'))}")
        # 找 next 链接
        nxt = sp2.select_one("a[rel=next], a.next, li.next a, a[aria-label*=Next]")
        print(f"     next 选择器命中: {nxt.get('href') if nxt else '(无)'}")

    # ---------- 2) kkmzt 分页 ----------
    print("=" * 78)
    print("■ kkmzt 分页")
    base = "https://kkmzt.com/photo/169171"
    for cand in [base, base + "/2", base + "?page=2", base + "/p/2"]:
        try:
            r = get(s, cand, False)
            sp = BeautifulSoup(r.text, "lxml")
            main_img = sp.select("figure.uk-inline img, div.uk-card-media img")
            srcs = []
            for im in sp.select("img"):
                v = im.get("data-src") or im.get("src") or ""
                if "/image/" in v:
                    srcs.append(v)
            print(f"  {cand} → {r.status_code}  主图数={len(srcs)}  {srcs[:3]}")
            print(f"      thumb-h 链接: {[a.get('href') for a in sp.select('a.uk-inline.u-thumb-h')][:5]}")
            print(f"      thumb-f 链接: {[a.get('href') for a in sp.select('a.uk-inline.u-thumb-f')][:5]}")
        except Exception as e:
            print(f"  {cand} → ✗ {type(e).__name__}: {e}")

    # ---------- 3) yituyu 分页 ----------
    print("=" * 78)
    print("■ yituyu 分页")
    for cand in ["https://www.yituyu.com/gallery/15793/",
                 "https://www.yituyu.com/gallery/15793/2/",
                 "https://www.yituyu.com/gallery/15793/?page=2"]:
        try:
            r = get(s, cand, False)
            sp = BeautifulSoup(r.text, "lxml")
            n = len(sp.select("div.gallerypic img"))
            allsrc = [im.get("data-src") or im.get("src") for im in sp.select("div.gallerypic img")]
            print(f"  {cand} → {r.status_code}  gallerypic={n}  {allsrc[:6]}")
            # 找 JSON/JS 里的图片数组
            m = re.findall(r"gallery/15793/(\d+)_", r.text)
            print(f"      页面内出现的 pic 序号: {sorted(set(int(x) for x in m))[:20]}")
        except Exception as e:
            print(f"  {cand} → ✗ {type(e).__name__}: {e}")

    # ---------- 4) misskon 分页 ----------
    print("=" * 78)
    print("■ misskon 分页")
    base = "https://misskon.com/117974-yeha-school-nurse-219-photos/"
    for cand in [base, base + "2/", base + "page/2/", base + "?page=2"]:
        try:
            r = get(s, cand, True)
            sp = BeautifulSoup(r.text, "lxml")
            n = len(sp.select("img.aligncenter.lazy"))
            print(f"  {cand} → {r.status_code}  HTTP-200?  aligncenter={n}")
            for a in sp.select("a[href]"):
                t = a.get_text(strip=True)
                if t.isdigit() or "next" in t.lower() or "»" in t or "›" in t:
                    print(f"      pag a: {a.get('href')}  '{t[:10]}'")
        except Exception as e:
            print(f"  {cand} → ✗ {type(e).__name__}: {e}")

    # ---------- 5) taotu 缩略图 → 原图 ----------
    print("=" * 78)
    print("■ taotu 缩略图 vs 原图")
    thumb = ("https://res.taotu.org/hot-girls/%e5%b0%8f%e8%94%a1%e5%a4%b4%e5%96%b5%e5%96%b5%e5%96%b5/"
             "00069-%e9%bb%91%e4%b8%9d%e8%be%85%e5%af%bc%e5%91%98-29p/thumbnail/0001.jpg")
    full = thumb.replace("/thumbnail/", "/")
    ref = ("https://taotu.org/hot-girls/%e5%b0%8f%e8%94%a1%e5%a4%b4%e5%96%b5%e5%96%b5%e5%96%b5/"
           "00069-%e9%bb%91%e4%b8%9d%e8%be%85%e5%af%bc%e5%91%98-29p/")
    import time
    for label, u in (("thumbnail", thumb), ("full", full)):
        try:
            t0 = time.time()
            r = get(s, u, True, referer=ref)
            print(f"  {label}: HTTP {r.status_code}  {len(r.content)/1024:.0f} KB  CT={r.headers.get('Content-Type')}  {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"  {label}: ✗ {type(e).__name__}: {e}")

    # ---------- 6) 4kup 翻页 ----------
    print("=" * 78)
    print("■ 4kup 列表翻页 + 详情页分页")
    for cand in ["https://4kup.net/page/2/", "https://4kup.net/le-ledg-209b-gms/2/",
                 "https://4kup.net/le-ledg-209b-gms/"]:
        try:
            r = get(s, cand, False)
            sp = BeautifulSoup(r.text, "lxml")
            print(f"  {cand} → {r.status_code}  img.lazy={len(sp.select('img.lazy'))}  a.thumb-photo={len(sp.select('a.thumb-photo'))}")
        except Exception as e:
            print(f"  {cand} → ✗ {type(e).__name__}: {e}")
    r = get(s, "https://4kup.net/le-ledg-209b-gms/", False)
    sp = BeautifulSoup(r.text, "lxml")
    print("  4kup 详情页分页导航:")
    for a in sp.select("div.pagination a, nav.pagination a, a.page-numbers, a.next"):
        print(f"      {a.get('href')}  '{a.get_text(strip=True)[:12]}'")

    # ---------- 7) ilovexs 翻页 ----------
    print("=" * 78)
    print("■ ilovexs 翻页")
    r = get(s, "https://ilovexs.com/", False)
    sp = BeautifulSoup(r.text, "lxml")
    for a in sp.select("nav.pagination-shell a, a.pagination-link"):
        print(f"      {a.get('href')}  '{a.get_text(strip=True)[:14]}' rel={a.get('rel')}")

    # ---------- 8) ilovexs / 4kup 图片是否防盗链 ----------
    print("=" * 78)
    print("■ 防盗链测试（不带 Referer 直接取图）")
    tests = [
        ("ilovexs img", "https://reiobox.top/wp-content/uploads/2026/09/GRA09V196_1.webp", False),
        ("4kup img", "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEj330jrJzH0WP7FqGpLBTT70rjjztNhyphenhyphenMJHLnDInAXtcSFOL5a6qzk_tPkl4BPS72KUBXfQ38uwhjzjy951Kcpd_X5OkODULqpP2tUZXLTl4vcjq3KJ4GNeJp4hZs-CGywaPiq75ycInwwoK3iICmYbeMkiLNk9CD8gN2EAOGFzlvFd0ARSX7eyqRI9JL4/h600-e30/LE-LEDG-209B-GMS-33-4kUp-109.webp", False),
        ("v2ph img", "https://cdn.v2ph.com/photos/ut7UQOMNznP5GKkh.jpg", True),
        ("misskon img", "https://pok.misskon.com/imghost/uploads/2026/09/18/Yeha-Your-Majesty-School-Nurse-MissKON.com-004.WiAwImEm.webp", True),
        ("taotu full", full, True),
    ]
    for label, u, use_p in tests:
        try:
            r = get(s, u, use_p)   # 不带 Referer
            print(f"  {label}: HTTP {r.status_code}  {len(r.content)/1024:.0f} KB  CT={r.headers.get('Content-Type')}")
        except Exception as e:
            print(f"  {label}: ✗ {type(e).__name__}: {str(e)[:90]}")


if __name__ == "__main__":
    main()
