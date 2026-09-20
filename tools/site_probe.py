#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_probe.py —— 批量探测一批站点的首页/列表页结构，为写 photocrawl 规则提供依据。

用法：
  python tools/site_probe.py                 # 探测内置清单
  python tools/site_probe.py <url> [<url>]   # 探测指定地址

对每个站点输出：可达性、标题、<img>/<a> 数量、可用的列表条目选择器候选、
懒加载属性、疑似翻页链接、是否需要代理。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse as up
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from bs4 import BeautifulSoup

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SITES = [
    ("写真1 amlyu",        "https://amlyu.com/"),
    ("写真2 24ao",         "https://www.24ao.cc/"),
    ("写真3 shichuanling", "https://www.shichuanling.com/"),
    ("写真4 heisiku",      "https://www.heisiku.com/"),
    ("写真5 taotu",        "https://taotu.org/"),
    ("写真6 kkmzt",        "https://kkmzt.com/"),
    ("写真7 yituyu",       "https://www.yituyu.com/"),
    ("ilovexs",            "https://ilovexs.com/"),
    ("v2ph",               "https://www.v2ph.com/"),
    ("x-idol",             "http://x-idol.net/"),
    ("4kup",               "https://4kup.net/"),
    ("misskon",            "https://misskon.com/"),
    ("自制代理 shichuanling", "https://testx.dhxlsfn.dpdns.org/net/https/www.shichuanling.com"),
]

LAZY_KEYS = ["data-original", "data-src", "data-lazy-src", "data-echo", "data-url",
             "data-image", "data-full", "data-large", "srcset", "loading"]

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".jfif"}

DETAIL_HINT = re.compile(
    r"(album|gallery|photo|pic|image|show|model|article|view|post|thread|p/|detail|"
    r"nude|girl|actor|idol|star|beauty|cosplay|zhaopian|tupian|xiuren|hitao|show)",
    re.I,
)


def try_get(session, url, proxy=None, timeout=20):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = session.get(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                    timeout=timeout, allow_redirects=True, proxies=proxies, verify=False)
    return r


def probe(name, url, session, proxy):
    print("=" * 78)
    print(f"■ {name}  {url}")
    t0 = time.time()
    r = None
    err = ""
    try:
        r = try_get(session, url, proxy)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    dt = time.time() - t0

    if r is None:
        print(f"  ✗ 不可达（{dt:.1f}s）  {err}")
        return {"name": name, "url": url, "ok": False, "err": err}

    print(f"  ✓ HTTP {r.status_code}  {len(r.content)} bytes  {dt:.1f}s  final={r.url}")
    if r.status_code >= 400:
        print("  ✗ 状态码异常")
        return {"name": name, "url": url, "ok": False, "err": f"HTTP {r.status_code}"}

    r.encoding = r.apparent_encoding or r.encoding
    html = r.text
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else "(无)"
    print(f"  title: {title}")

    imgs = soup.find_all("img")
    links = [a for a in soup.find_all("a", href=True)]
    print(f"  <img>={len(imgs)}   <a>={len(links)}")

    # 懒加载属性统计
    lazy = Counter()
    for im in imgs:
        for k in im.attrs:
            if k in LAZY_KEYS:
                lazy[k] += 1
    if lazy:
        print("  懒加载属性: " + ", ".join(f"{k}×{v}" for k, v in lazy.most_common()))

    # 图片宿主统计（看图片是否走 CDN）
    hosts = Counter()
    for im in imgs[:80]:
        v = im.get("data-original") or im.get("data-src") or im.get("src") or ""
        if v:
            try:
                hosts[up.urlparse(up.urljoin(url, v.strip())).netloc] += 1
            except Exception:
                pass
    if hosts:
        print("  图片域名: " + ", ".join(f"{h}({n})" for h, n in hosts.most_common(5)))

    # 列表条目选择器候选：只看「像详情页」的链接
    cand_a = Counter()
    cand_parent = Counter()
    samples = []
    for a in links:
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        full = up.urljoin(url, href)
        p = up.urlparse(full)
        if p.netloc and p.netloc != up.urlparse(url).netloc:
            continue
        path = p.path
        if path.rstrip("/") == up.urlparse(url).path.rstrip("/"):
            continue
        if DETAIL_HINT.search(path) or len([s for s in path.split("/") if s]) >= 2:
            akey = a.name + ("." + ".".join(a.get("class", [])[:2]) if a.get("class") else "")
            cand_a[akey] += 1
            par = a.parent
            if par is not None and par.name not in ("body", "[document]"):
                pk = par.name + ("." + ".".join(par.get("class", [])[:2]) if par.get("class") else "")
                cand_parent[pk] += 1
                if len(samples) < 6:
                    samples.append((full, a.get("title") or a.get_text(" ", strip=True)[:40], pk))

    if cand_a:
        print("  ── 条目 <a> 选择器候选（按出现次数）──")
        for k, n in cand_a.most_common(6):
            print(f"      {n:>4}×  {k}")
    if cand_parent:
        print("  ── 条目父容器选择器候选 ──")
        for k, n in cand_parent.most_common(8):
            print(f"      {n:>4}×  {k}")
    if samples:
        print("  ── 条目样例 ──")
        for u, t, pk in samples:
            print(f"      [{pk}] {u}    「{t}」")

    # 翻页候选
    pag = []
    for a in links:
        txt = (a.get_text(" ", strip=True) or "").lower()
        cls = " ".join(a.get("class", []))
        rel = a.get("rel", [])
        if ("next" in txt or "下一页" in txt or "下一頁" in txt or "»" in txt
                or "next" in cls.lower() or "next" in rel or "pager" in cls.lower()):
            pag.append(f"{up.urljoin(url, a['href'])}  「{txt[:16]}」")
    if pag:
        print("  ── 翻页候选 ──")
        for s in pag[:5]:
            print(f"      {s}")

    # 疑似 JS 渲染判断
    if len(imgs) <= 3 and len(links) > 10:
        print("  ⚠️  图片极少但链接不少 —— 可能是 JS 渲染（需读接口）")
    if re.search(r"__NEXT_DATA__|window\.__NUXT__|window\._cfg", html):
        print("  ⚠️  检测到前端框架数据（Next/Nuxt）—— 可能要走接口")

    # 输出关键词，判断内容定位
    kw = []
    for k in ["无码", "有码", "成人", "裸露", "色情", "NSFW", "18+", "写真", "套图",
              "模特", "丝袜", "泳装", "制服", "cosplay", "少女"]:
        if k in html:
            kw.append(k)
    if kw:
        print("  ⚠️  页面关键词: " + " / ".join(kw))

    return {"name": name, "url": url, "ok": True, "title": title,
            "imgs": len(imgs), "links": len(links),
            "list_cand": [k for k, _ in cand_a.most_common(6)],
            "parent_cand": [k for k, _ in cand_parent.most_common(8)]}


def main() -> int:
    args = sys.argv[1:]
    sites = [(f"自定义{i}", u) for i, u in enumerate(args, 1)] if args else SITES

    sess = requests.Session()
    sess.verify = False
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass

    results = []
    for name, url in sites:
        rec = None
        # 先直连（本机有透明代理时其实也是走代理）
        for proxy in (None, "http://127.0.0.1:7890"):
            try:
                rec = probe(name, url, sess, proxy)
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ 探测异常: {type(e).__name__}: {e}")
                rec = {"name": name, "url": url, "ok": False, "err": str(e)}
            if rec.get("ok"):
                rec["via"] = "direct" if proxy is None else "proxy7890"
                break
            print(f"  ↻ 重试（换代理 {proxy or '直连'}）…")
        results.append(rec or {"name": name, "url": url, "ok": False})

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_probe_result.json"),
              "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print("汇总：")
    for r in results:
        if r.get("ok"):
            print(f"  ✓ {r['name']:<22} imgs={r.get('imgs'):>4}  links={r.get('links'):>4}  via={r.get('via')}")
        else:
            print(f"  ✗ {r['name']:<22} {r.get('err', '')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
