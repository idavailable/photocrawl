#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detail_probe.py —— 对每个写真站，自动挑出「图集详情页」并分析其图片结构。

目的：确定 rule 里的 detail_images / image_attrs / gallery_next / referer 怎么填。
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

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

PROXY = "http://127.0.0.1:7890"

SITES = [
    dict(name="taotu",        list_url="https://taotu.org/",              proxy=True),
    dict(name="kkmzt",        list_url="https://kkmzt.com/photo/",        proxy=False),
    dict(name="yituyu",       list_url="https://www.yituyu.com/gallery/", proxy=False),
    dict(name="misskon",      list_url="https://misskon.com/",            proxy=True),
    dict(name="v2ph",         list_url="https://www.v2ph.com/",           proxy=True),
    dict(name="ilovexs",      list_url="https://ilovexs.com/",            proxy=False),
    dict(name="24ao",         list_url="https://www.24ao.cc/Articles",    proxy=False),
    dict(name="amlyu",        list_url="https://amlyu.com/",              proxy=False),
    dict(name="shichuanling", list_url="https://www.shichuanling.com/",   proxy=True),
    dict(name="heisiku",      list_url="https://www.heisiku.com/",        proxy=False),
    dict(name="4kup",         list_url="https://4kup.net/",               proxy=False),
    dict(name="x-idol",       list_url="http://x-idol.net/",              proxy=False),
]

BAD = re.compile(
    r"/(category|tag|categories|tags|page|author|user|about|contact|login|register|signup|"
    r"vip|member|search|help|faq|privacy|terms|dmca|feed|rss|comment|cart|shop|app|download|"
    r"file|desktop|uploads?/vip|link|links|friend|advert|notice|announce)(/|$|\?|\.)",
    re.I,
)

NAV_TEXT = {"首页", "home", "更多", "全部", "next", "prev", "下一页", "上一页", "登录",
            "注册", "标签", "分类", "关于", "联系", "更多内容", "查看更多"}


def get(sess, url, proxy, referer=""):
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if referer:
        h["Referer"] = referer
    p = {"http": proxy, "https": proxy} if proxy else None
    return sess.get(url, headers=h, timeout=25, proxies=p, verify=False, allow_redirects=True)


def candidate_details(soup, base):
    """从列表页挑出可能是「图集详情页」的链接。"""
    host = up.urlparse(base).netloc
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        full = up.urljoin(base, href)
        p = up.urlparse(full)
        if p.netloc != host:
            continue
        if BAD.search(p.path):
            continue
        segs = [s for s in p.path.split("/") if s]
        if not segs:
            continue
        # 末段必须是「有意义的 slug」或纯数字 id / xxx.html，而不是栏目名
        last = segs[-1]
        if last in ("", "index.html", "index.php"):
            continue
        txt = a.get_text(" ", strip=True)
        if txt in NAV_TEXT or len(txt) > 120:
            continue
        looks_detail = (
            re.search(r"\d{3,}", last)                 # 含数字 id
            or last.endswith(".html")
            or len(segs) >= 2
        )
        if not looks_detail:
            continue
        k = f"{p.netloc}{p.path}"
        if k in seen:
            continue
        seen.add(k)
        score = 0
        if re.search(r"(album|gallery|photo|pic|show|article|content|thread|post|models?|girls?|"
                     r"nude|idol|star|beauty)", p.path, re.I):
            score += 2
        if txt:
            score += 1
        if re.search(r"\d{3,}", last):
            score += 1
        out.append((score, full, txt, a))
    out.sort(key=lambda x: -x[0])
    return out


def analyse_detail(sess, url, proxy, referer):
    print(f"\n  详情页: {url}")
    r = get(sess, url, proxy, referer=referer)
    print(f"    HTTP {r.status_code}  {len(r.content)}B")
    if r.status_code >= 400:
        return None
    r.encoding = r.apparent_encoding or r.encoding
    html = r.text
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""

    imgs = soup.find_all("img")
    print(f"    <img> = {len(imgs)}")

    hosts = Counter()
    kvs = Counter()
    big = []
    for im in imgs:
        v = ""
        for k in ("data-original", "data-src", "data-lazy-src", "data-echo",
                  "data-url", "data-image", "src"):
            if im.get(k):
                kvs[k] += 1
                if not v:
                    v = im[k]
        if v:
            full = up.urljoin(url, v.strip())
            hosts[up.urlparse(full).netloc] += 1
            try:
                w = int(im.get("width") or 0)
                h = int(im.get("height") or 0)
            except Exception:
                w = h = 0
            if w >= 500 or h >= 500 or (w == 0 and h == 0):
                big.append((full, w, h, ".".join(im.get("class", [])[:2])))
    if kvs:
        print("    取图属性: " + ", ".join(f"{k}×{v}" for k, v in kvs.most_common()))
    if hosts:
        print("    图片域名: " + ", ".join(f"{h}({n})" for h, n in hosts.most_common(6)))
    print(f"    其中「疑似大图」{len(big)} 张，样例：")
    for u, w, h, c in big[:8]:
        print(f"      {w}x{h:<6} [{c}] {u}")

    # 图片父容器 class 统计（挑 detail_images 选择器用）
    pc = Counter()
    for im in imgs:
        par = im.parent
        if par is not None:
            c = ".".join(par.get("class", [])[:2])
            pc[f"{par.name}.{c} img" if c else f"{par.name} img"] += 1
        c2 = ".".join(im.get("class", [])[:2])
        if c2:
            pc[f"img.{c2}"] += 1
    print("    选择器候选: " + " | ".join(f"{k}({n})" for k, n in pc.most_common(8)))

    # 图集内翻页
    pag = []
    for a in soup.find_all("a", href=True):
        t = (a.get_text(" ", strip=True) or "").lower()
        cls = " ".join(a.get("class", [])).lower()
        if any(k in t for k in ("下一页", "下一頁", "next", "»", "›")) or "next" in cls:
            pag.append(up.urljoin(url, a["href"]))
    if pag:
        print("    翻页候选: " + " ; ".join(pag[:4]))

    # 是否被登录墙挡住
    for k in ("登录后", "请登录", "VIP", "会员", "购买", "金币", "积分", "注册后"):
        if k in html:
            print(f"    ⚠️  含「{k}」字样 —— 可能有关卡")

    return {"url": url, "title": title, "imgs": len(imgs),
            "hosts": [h for h, _ in hosts.most_common(6)],
            "big": len(big), "sample": [b[0] for b in big[:5]]}


def main():
    sess = requests.Session()
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass

    report = []
    for site in SITES:
        name, lu = site["name"], site["list_url"]
        proxy = PROXY if site["proxy"] else None
        print("=" * 78)
        print(f"■ {name}  列表页 {lu}   (proxy={bool(proxy)})")
        try:
            r = get(sess, lu, proxy)
            r.encoding = r.apparent_encoding or r.encoding
            soup = BeautifulSoup(r.text, "lxml")
        except Exception as e:
            print(f"  ✗ 列表页失败 {type(e).__name__}: {e}")
            report.append({"site": name, "error": str(e)})
            continue

        cands = candidate_details(soup, lu)
        print(f"  候选详情页 {len(cands)} 个，取前 2 个分析：")
        for i, (sc, u, t, _a) in enumerate(cands[:6], 1):
            print(f"    {i}. [{sc}] {u}   「{t[:40]}」")

        details = []
        for sc, u, t, _a in cands[:2]:
            try:
                d = analyse_detail(sess, u, proxy, lu)
                if d:
                    d["from_list"] = lu
                    details.append(d)
            except Exception as e:
                print(f"    ✗ 详情页失败 {type(e).__name__}: {e}")
            time.sleep(0.4)
        report.append({"site": name, "list_url": lu, "cands": [c[1] for c in cands[:10]],
                       "details": details})

    outp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "detail_probe_result.json")
    with open(outp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n结果已写入 {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
