#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""final_probe2.py —— 剩余确认项（带容错）。"""

from __future__ import annotations

import os
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

s = requests.Session()
try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass
s.headers.update({"User-Agent": UA})


def get(url, use_proxy=False, referer="", timeout=20):
    p = {"http": PROXY, "https": PROXY} if use_proxy else None
    h = {"Referer": referer} if referer else {}
    return s.get(url, headers=h, timeout=timeout, proxies=p, verify=False, allow_redirects=True)


def safe(label, fn):
    print(f"\n--- {label} ---")
    try:
        fn()
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {str(e)[:120]}")


# 1) 4kup 详情页 a.thumb-photo href + 列表分页
def f_4kup():
    for use_p in (False, True):
        try:
            r = get("https://4kup.net/le-ledg-209b-gms/", use_p, timeout=25)
            sp = BeautifulSoup(r.text, "lxml")
            hs = [a.get("href") for a in sp.select("a.thumb-photo")]
            print(f"  proxy={use_p} a.thumb-photo={len(hs)} hrefs={hs[:5]}")
            print(f"  img.lazy={len(sp.select('img.lazy'))}  a.post-image={len(sp.select('a.post-image'))}")
            print(f"  pagination: {[a.get('href') for a in sp.select('.pagination a, a.page-numbers, a.next, a.prev')][:8]}")
            break
        except Exception as e:
            print(f"  proxy={use_p} ✗ {type(e).__name__}")


safe("4kup 详情页链接", f_4kup)


def f_4kup_list():
    for u in ["https://4kup.net/", "https://4kup.net/page/2/", "https://4kup.net/models/"]:
        try:
            r = get(u, False, timeout=25)
            sp = BeautifulSoup(r.text, "lxml")
            titles = [a.get("href") for a in sp.select("h2.entry-title a")]
            print(f"  {u} → {r.status_code} entry-title={len(titles)} post-image={len(sp.select('a.post-image'))}")
            print(f"      titles={titles[:4]}")
            print(f"      pag={[a.get('href') for a in sp.select('.pagination a, a.page-numbers, a.next, a.post-page-numbers')][:8]}")
        except Exception as e:
            print(f"  {u} ✗ {type(e).__name__}: {str(e)[:60]}")


safe("4kup 列表页", f_4kup_list)

# 2) taotu 外层 a href
TAOTU = ("https://taotu.org/hot-girls/%e5%b0%8f%e8%94%a1%e5%a4%b4%e5%96%b5%e5%96%b5%e5%96%b5/"
         "00069-%e9%bb%91%e4%b8%9d%e8%be%85%e5%af%bc%e5%91%98-29p/")


def f_taotu():
    r = get(TAOTU, True, timeout=25)
    sp = BeautifulSoup(r.text, "lxml")
    pairs = []
    for a in sp.find_all("a", href=True):
        img = a.find("img")
        if img is None:
            continue
        pairs.append((a["href"], img.get("src") or img.get("data-src") or ""))
    print(f"  含图 <a> {len(pairs)} 个")
    for h, src in pairs[:5]:
        print(f"    href={h}")
        print(f"      src={src}")
    # a href 里是否有图片
    imgs = [h for h, _ in pairs if up.urlparse(h).path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    print(f"  href 本身是图片的: {len(imgs)}  样例={imgs[:3]}")


safe("taotu 外层 a", f_taotu)


def f_taotu_variants():
    b = ("https://res.taotu.org/hot-girls/%e5%b0%8f%e8%94%a1%e5%a4%b4%e5%96%b5%e5%96%b5%e5%96%b5/"
         "00069-%e9%bb%91%e4%b8%9d%e8%be%85%e5%af%bc%e5%91%98-29p/")
    for path in ["0001.jpg", "large/0001.jpg", "big/0001.jpg", "images/0001.jpg",
                 "orig/0001.jpg", "001.jpg", "01.jpg"]:
        try:
            r = get(b + path, True, referer=TAOTU, timeout=15)
            print(f"  {path:<18} → {r.status_code} {len(r.content)/1024:.1f}KB {r.headers.get('Content-Type')}")
        except Exception as e:
            print(f"  {path:<18} → ✗ {type(e).__name__}")


safe("taotu 原图路径变体", f_taotu_variants)

# 3) misskon 分页
MK = "https://misskon.com/117974-yeha-school-nurse-219-photos/"


def f_misskon():
    for pg in [MK, MK + "1/", MK + "2/", MK + "5/", MK + "18/", MK + "20/"]:
        try:
            r = get(pg, True, timeout=25)
            sp = BeautifulSoup(r.text, "lxml")
            us = [im.get("data-src") or im.get("src") for im in sp.select("img.aligncenter.lazy")]
            print(f"  {pg.split('/')[-2] if pg.endswith('/') else '(base)':<8} → {r.status_code} n={len(us)} first={us[0].split('/')[-1][-24:] if us else '-'}")
        except Exception as e:
            print(f"  {pg} ✗ {type(e).__name__}")
    r = get(MK, True, timeout=25)
    sp = BeautifulSoup(r.text, "lxml")
    pag = []
    for a in sp.find_all("a", href=True):
        t = a.get_text(strip=True)
        if t.isdigit() or t in ("下一页", "»", "›", "Next"):
            pag.append((a["href"], t))
    print(f"  分页链接: {pag[:10]}")
    print(f"  分页容器 class: {[c.get('class') for c in sp.select('.pagination, nav.pagination, div.pagination')][:4]}")


safe("misskon 分页", f_misskon)

# 4) v2ph
def f_v2ph():
    alb = "https://www.v2ph.com/album/YTY-2152"
    for pg in [alb, alb + "?page=2", alb + "?page=3"]:
        r = get(pg, True, referer=alb, timeout=25)
        sp = BeautifulSoup(r.text, "lxml")
        us = [im.get("src") or im.get("data-src") for im in sp.select("img.album-photo")]
        us2 = [im.get("src") or im.get("data-src") for im in sp.select("img")]
        print(f"  {pg[-12:]:<12} → {r.status_code} album-photo={len(us)} allimg={len(us2)} {[up.urlparse(x).path.split('/')[-1] for x in us[:3] if x]}")
    r = get(alb, True, timeout=25)
    sp = BeautifulSoup(r.text, "lxml")
    for a in sp.select("ul.pagination a, nav.py-2 a"):
        print(f"    pag a: {a.get('href')} '{a.get_text(strip=True)}'")


safe("v2ph 分页", f_v2ph)


def f_v2ph_list():
    r = get("https://www.v2ph.com/", True, timeout=25)
    sp = BeautifulSoup(r.text, "lxml")
    ms = [a.get("href") for a in sp.select("a.media-cover")]
    print(f"  a.media-cover={len(ms)}  {ms[:4]}")
    cards = sp.select("div.card-cover a, a.media-cover")
    print(f"  全部候选={len(cards)}")
    for a in sp.select("ul.pagination a"):
        print(f"    pag: {a.get('href')} '{a.get_text(strip=True)}'")


safe("v2ph 列表", f_v2ph_list)


# 5) ilovexs
def f_ilovexs():
    r = get("https://ilovexs.com/", False, timeout=25)
    sp = BeautifulSoup(r.text, "lxml")
    arts = [a.get("href") for a in sp.select("article.album-card a.album-card-link")]
    print(f"  album-card-link={len(arts)} {arts[:4]}")
    print(f"  article.album-card={len(sp.select('article.album-card'))}")
    for a in sp.select("nav.pagination-shell a, a.pagination-link"):
        print(f"    pag: {a.get('href')} '{a.get_text(strip=True)}'")
    # 分类页
    for cat in ["https://ilovexs.com/category/gravure/", "https://ilovexs.com/category/japan/"]:
        r2 = get(cat, False, timeout=25)
        sp2 = BeautifulSoup(r2.text, "lxml")
        print(f"  {cat} → {r2.status_code} album={len(sp2.select('article.album-card'))}")


safe("ilovexs 列表", f_ilovexs)
