#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""final_probe.py —— 最后一轮：确认 4kup/misskon/v2ph 分页真伪 + taotu 原图取法。"""

from __future__ import annotations

import os
import re
import sys
import urllib.parse as up
from collections import Counter

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
s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})


def get(url, use_proxy, referer=""):
    p = {"http": PROXY, "https": PROXY} if use_proxy else None
    h = {"Referer": referer} if referer else {}
    return s.get(url, headers=h, timeout=30, proxies=p, verify=False, allow_redirects=True)


print("=" * 78)
print("■ 4kup 详情页分页是否真翻页")
prev = None
for pg in ["https://4kup.net/le-ledg-209b-gms/", "https://4kup.net/le-ledg-209b-gms/2/",
           "https://4kup.net/le-ledg-209b-gms/3/"]:
    r = get(pg, False)
    sp = BeautifulSoup(r.text, "lxml")
    urls = [im.get("data-src") or im.get("src") for im in sp.select("a.thumb-photo img, img.lazy")]
    urls = [u for u in urls if u]
    tail = [up.urlparse(u).path.split("/")[-1] for u in urls][:3]
    same = (urls == prev)
    print(f"  {pg} → {r.status_code} n={len(urls)} same_as_prev={same}  {tail}")
    prev = urls
# 看 a.thumb-photo 的 href 指向
r = get("https://4kup.net/le-ledg-209b-gms/", False)
sp = BeautifulSoup(r.text, "lxml")
print("  a.thumb-photo href 样例:", [a.get("href") for a in sp.select("a.thumb-photo")][:4])
print("  列表分页 a.page-numbers:", [a.get("href") for a in sp.select("a.page-numbers, .pagination a")][:6])
r2 = get("https://4kup.net/page/2/", False)
sp2 = BeautifulSoup(r2.text, "lxml")
print(f"  4kup 列表 /page/2/ → {r2.status_code} entry-title={len(sp2.select('h2.entry-title'))} post-image={len(sp2.select('a.post-image'))}")
print("  列表条目链接:", [a.get("href") for a in sp2.select("h2.entry-title a")][:5])

print("=" * 78)
print("■ taotu 缩略图外层 <a> 指向哪里")
u = ("https://taotu.org/hot-girls/%e5%b0%8f%e8%94%a1%e5%a4%b4%e5%96%b5%e5%96%b5%e5%96%b5/"
     "00069-%e9%bb%91%e4%b8%9d%e8%be%85%e5%af%bc%e5%91%98-29p/")
r = get(u, True)
sp = BeautifulSoup(r.text, "lxml")
hrefs = []
for a in sp.find_all("a", href=True):
    img = a.find("img")
    if img is not None:
        hrefs.append((a["href"], img.get("src") or img.get("data-src") or ""))
print(f"  含图的 <a> 共 {len(hrefs)} 个，样例：")
for h, src in hrefs[:6]:
    print(f"    href={h}")
    print(f"      img={src}")

print("=" * 78)
print("■ 尝试 taotu 原图路径变体")
base = ("https://res.taotu.org/hot-girls/%e5%b0%8f%e8%94%a1%e5%a4%b4%e5%96%b5%e5%96%b5%e5%96%b5/"
        "00069-%e9%bb%91%e4%b8%9d%e8%be%85%e5%af%bc%e5%91%98-29p/")
ref = u
for path in ["0001.jpg", "large/0001.jpg", "big/0001.jpg", "images/0001.jpg",
             "thumbnail/0001.jpg", "001.jpg", "1.jpg"]:
    try:
        r = get(base + path, True, referer=ref)
        print(f"  {path:<22} → {r.status_code}  {len(r.content)/1024:.1f}KB  {r.headers.get('Content-Type')}")
    except Exception as e:
        print(f"  {path:<22} → ✗ {type(e).__name__}")

print("=" * 78)
print("■ misskon /1/ 是否可用")
for pg in ["https://misskon.com/117974-yeha-school-nurse-219-photos/",
           "https://misskon.com/117974-yeha-school-nurse-219-photos/1/",
           "https://misskon.com/117974-yeha-school-nurse-219-photos/5/"]:
    r = get(pg, True)
    sp = BeautifulSoup(r.text, "lxml")
    urls = [im.get("data-src") or im.get("src") for im in sp.select("img.aligncenter.lazy")]
    print(f"  {pg} → {r.status_code} n={len(urls)}  first={urls[0].split('/')[-1] if urls else '-'}")
    pag = [(a.get("href"), a.get_text(strip=True)) for a in sp.select(".pagination a, a.page-link, a.page")]
    print(f"     pagination: {pag[:8]}")

print("=" * 78)
print("■ v2ph 带 Referer 翻页")
alb = "https://www.v2ph.com/album/YTY-2152"
for pg in [alb, alb + "?page=2", alb + "/2", alb + "?page=2&x=1"]:
    r = get(pg, True, referer=alb)
    sp = BeautifulSoup(r.text, "lxml")
    urls = [im.get("src") or im.get("data-src") for im in sp.select("img.img-fluid.album-photo")]
    print(f"  {pg} → {r.status_code} n={len(urls)}  {[up.urlparse(x).path.split('/')[-1] for x in urls[:3] if x]}")
# 找真正的 next 链接（在整个分页 nav 里）
r = get(alb, True)
sp = BeautifulSoup(r.text, "lxml")
nav = sp.select_one("nav.py-2, ul.pagination")
if nav:
    for a in nav.select("a"):
        print(f"    a: href={a.get('href')} text='{a.get_text(strip=True)}' class={a.get('class')}")

print("=" * 78)
print("■ v2ph 列表页条目 + 翻页")
r = get("https://www.v2ph.com/", True)
sp = BeautifulSoup(r.text, "lxml")
print("  a.media-cover:", len(sp.select("a.media-cover")), [a.get("href") for a in sp.select("a.media-cover")][:4])
for a in sp.select("ul.pagination a, nav a"):
    if "page" in (a.get("href") or ""):
        print(f"    pag: {a.get('href')} '{a.get_text(strip=True)}'")

print("=" * 78)
print("■ ilovexs 列表页条目 + 专辑图片数抽样")
r = get("https://ilovexs.com/", False)
sp = BeautifulSoup(r.text, "lxml")
arts = sp.select("article.album-card a.album-card-link")
print("  album-card-link:", len(arts), [a.get("href") for a in arts][:4])
for a in sp.select("nav.pagination-shell a, a.pagination-link"):
    print(f"    pag: {a.get('href')} '{a.get_text(strip=True)}'")
