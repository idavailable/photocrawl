#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
photocrawl —— 图集/写真类站点通用爬虫

设计思路：引擎与站点规则分离。换站点只需要写一份 JSON 规则（rules/*.json），
不用改代码。规则里描述三件事：
  1) 从哪几页拿到「图集」的链接（列表页 + 翻页）
  2) 从图集详情页怎么抽出图片地址
  3) 一些过滤/命名的小选项

常用命令：
  # 0) 探测一个页面，看它有哪些图片和链接（写规则前第一步）
  python crawl.py probe "https://example.com/gallery/123"

  # 1) 试跑：只列出将要下载的内容，不落盘
  python crawl.py run --rule rules/demo.json --dry-run

  # 2) 正式下载
  python crawl.py run --rule rules/demo.json --out ./下载 --workers 8

  # 3) 已有链接清单，直接下载
  python crawl.py run --from-file my_urls.txt --out ./下载

只想快速扒一个页面上的图，可以不要规则：
  python crawl.py run --auto "https://example.com/gallery/123" --out ./下载
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse as up
import urllib.robotparser as robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 requests，请先安装：pip install requests beautifulsoup4 lxml")

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 beautifulsoup4，请先安装：pip install requests beautifulsoup4 lxml")

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 常见的懒加载属性，按优先级尝试
LAZY_ATTRS = [
    "data-original",
    "data-original-src",
    "data-src",
    "data-lazy-src",
    "data-echo",
    "data-url",
    "data-image",
    "data-full",
    "data-large",
    "data-hi-res-src",
    "src",
]

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".jfif"}

# 明显不是正文图的路径关键词（头像、图标、广告位等）
# 注意：ads?/ 必须带路径边界，否则会把 uploads/ 里的 "ads/" 误判成广告目录
JUNK_PAT = re.compile(
    r"(avatar|logo|icon|sprite|placeholder|loading|blank|spacer|pixel|"
    r"advert|(?:^|/)ads?/|banner|button|emoji|favicon|thumb[-_]?icon)",
    re.I,
)

ILLEGAL_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def log(msg: str) -> None:
    print(msg, flush=True)


def resolve_proxy(value: str) -> tuple[str | None, bool]:
    """
    归一化 --proxy 的取值，返回 (代理地址, 是否忽略环境变量代理)。

      local / on / default  → 本地 127.0.0.1:7890
      none / off / direct / 0 → 真·直连（同时忽略环境变量里的 HTTP(S)_PROXY）
      其它非空字符串         → 当作代理地址
      空                    → 交给环境变量
    """
    v = (value or "").strip()
    if not v:
        return None, False
    low = v.lower()
    if low in ("local", "on", "default"):
        return "http://127.0.0.1:7890", False
    if low in ("none", "off", "no", "direct", "0"):
        return None, True
    return v, False


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------


def abs_url(base: str, href: str) -> str:
    """把相对地址补成绝对地址；顺便去掉 fragment。"""
    if not href:
        return ""
    href = href.strip()
    if href.startswith(("data:", "javascript:", "about:", "#")):
        return ""
    # 协议相对：//cdn.example.com/a.jpg
    if href.startswith("//"):
        scheme = up.urlparse(base).scheme or "https"
        href = f"{scheme}:{href}"
    out = up.urljoin(base, href)
    return out.split("#", 1)[0]


def first_srcset(value: str) -> str:
    """从 srcset 里挑第一个候选地址。"""
    if not value:
        return ""
    return value.split(",")[0].strip().split(" ")[0]


def pick_url(tag, attrs: Iterable[str] | None = None) -> str:
    """按优先级从标签里取出图片地址，兼顾懒加载与 srcset。"""
    for a in (attrs or LAZY_ATTRS):
        v = tag.get(a)
        if v and isinstance(v, str) and v.strip():
            return v.strip()
    # <picture><source srcset=...>
    for src in tag.select("source[srcset]"):
        v = first_srcset(src.get("srcset", ""))
        if v:
            return v
    v = tag.get("srcset")
    if v:
        return first_srcset(v)
    return ""


def slugify(text: str, fallback: str = "item", maxlen: int = 80) -> str:
    """把标题变成安全的文件名/目录名。"""
    text = (text or "").strip()
    text = re.sub(r"\s+", "_", text)
    text = ILLEGAL_FS.sub("", text)
    text = text.strip("._ ")
    if not text:
        text = fallback
    return text[:maxlen]


def url_key(url: str) -> str:
    """用于去重的规范化地址：去掉 query（很多站用 ?w=300 生成缩略图）。"""
    p = up.urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


def apply_rewrites(url: str, rules: list | None) -> str:
    """按 [[正则, 替换], ...] 依次改写图片地址（如缩略图→原图）。"""
    if not url or not rules:
        return url
    for pair in rules:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        try:
            url = re.sub(str(pair[0]), str(pair[1]), url)
        except re.error as e:  # noqa: PERF203
            log(f"⚠️  image_rewrite 正则无效：{pair[0]}（{e}）")
    return url


def best_title(node, a, title_sel: str = "", maxlen: int = 120) -> str:
    """
    尽力取到「最完整」的标题。

    很多站点列表页的链接文字被服务器截断成 "XXX: A Story in ..."，
    真正的完整标题放在 `title` 属性或 `img[alt]` 里。这里把所有候选
    收集起来，优先选不以省略号结尾的、且最长的那个。
    """
    cands: list[str] = []
    if title_sel:
        t = node.select_one(title_sel)
        if t:
            cands.append(t.get_text(" ", strip=True))
    attr_title = a.get("title") or ""
    if attr_title:
        cands.append(attr_title)
    cands.append(node.get_text(" ", strip=True))
    for scope in (node, a):
        img = scope.select_one("img[alt]") if hasattr(scope, "select_one") else None
        if img and img.get("alt"):
            cands.append(img["alt"].strip())

    cands = [c for c in cands if c]
    if not cands:
        return ""
    complete = [c for c in cands if not c.endswith(("...", "…"))]
    pool = complete or cands
    return max(pool, key=len)[:maxlen]


def guess_ext(url: str, content_type: str = "") -> str:
    ext = os.path.splitext(up.urlparse(url).path)[1].lower()
    if ext in IMG_EXT:
        return ".jpg" if ext == ".jfif" else ext
    ct = (content_type or "").lower()
    for k, v in (
        ("jpeg", ".jpg"),
        ("png", ".png"),
        ("webp", ".webp"),
        ("gif", ".gif"),
        ("bmp", ".bmp"),
        ("avif", ".avif"),
    ):
        if k in ct:
            return v
    return ext or ".jpg"


def dig(obj: Any, path: str) -> list[Any]:
    """
    从 JSON 里按路径取值，支持 `a.b.0.c` 与 `a[*].b`。
    返回命中值组成的列表（可能为空）。
    """
    cur: list[Any] = [obj]
    for part in [p for p in path.split(".") if p != ""]:
        star = part.endswith("[*]")
        key = part[:-3] if star else part
        nxt: list[Any] = []
        for c in cur:
            v: Any = c
            if key:
                if isinstance(v, dict):
                    v = v.get(key)
                elif isinstance(v, list) and key.lstrip("-").isdigit():
                    i = int(key)
                    v = v[i] if -len(v) <= i < len(v) else None
                else:
                    v = None
            if star:
                if isinstance(v, list):
                    nxt.extend(v)
            elif v is not None:
                nxt.append(v)
        cur = nxt
        if not cur:
            break
    return cur


# ----------------------------------------------------------------------------
# 网络层
# ----------------------------------------------------------------------------


@dataclass
class Fetcher:
    proxy: str | None = None
    timeout: int = 20
    retries: int = 3
    delay: float = 0.0
    verify_robots: bool = True
    ignore_robots: bool = False
    no_env: bool = False  # True = 忽略环境变量里的 HTTP(S)_PROXY，真·直连

    def __post_init__(self) -> None:
        self._local = threading.local()
        self._robot_cache: dict[str, robotparser.RobotFileParser | None] = {}
        self._robot_lock = threading.Lock()
        self.stats = {"requests": 0, "errors": 0}

    # -- session per thread -------------------------------------------------
    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update(
                {
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
                    "Connection": "keep-alive",
                }
            )
            if self.proxy:
                s.proxies = {"http": self.proxy, "https": self.proxy}
                # 关键：环境变量里的 HTTP(S)_PROXY 优先级高于 session.proxies，
                # 不关掉 trust_env 的话显式指定的代理会被环境代理顶掉。
                s.trust_env = False
            elif self.no_env:
                s.trust_env = False
            self._local.s = s
        return s

    # -- robots -------------------------------------------------------------
    def robots_allows(self, url: str) -> bool:
        if not self.verify_robots or self.ignore_robots:
            return True
        parts = up.urlparse(url)
        root = f"{parts.scheme}://{parts.netloc}"
        with self._robot_lock:
            if root in self._robot_cache:
                rp = self._robot_cache[root]
            else:
                rp = None
                try:
                    r = self.session.get(root + "/robots.txt", timeout=self.timeout)
                    if r.status_code == 200:
                        rp = robotparser.RobotFileParser()
                        rp.parse(r.text.splitlines())
                    elif r.status_code in (401, 403):
                        rp = None  # 拒绝访问 robots，视为无限制
                except Exception:
                    rp = None
                self._robot_cache[root] = rp
        if rp is None:
            return True
        return rp.can_fetch(UA, url)

    # -- get ----------------------------------------------------------------
    def get(self, url: str, referer: str = "", binary: bool = False):
        if not self.robots_allows(url):
            raise PermissionError(f"robots.txt 不允许抓取：{url}")
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                headers = {}
                if referer:
                    headers["Referer"] = referer
                r = self.session.get(
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    stream=binary,
                    allow_redirects=True,
                )
                self.stats["requests"] += 1
                if r.status_code == 404:
                    r.close()
                    raise FileNotFoundError(f"404 {url}")
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code} {url}")
                if self.delay:
                    time.sleep(self.delay + random.uniform(0, self.delay * 0.5))
                return r
            except FileNotFoundError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                self.stats["errors"] += 1
                if attempt < self.retries:
                    time.sleep(min(2**attempt * 0.6, 8) + random.uniform(0, 0.4))
        raise RuntimeError(f"多次重试仍失败：{url}（{last}）")

    def soup(self, url: str, referer: str = "") -> tuple[BeautifulSoup, str]:
        r = self.get(url, referer=referer)
        r.encoding = r.apparent_encoding or r.encoding
        html = r.text
        r.close()
        parser = "lxml" if _HAS_LXML else "html.parser"
        return BeautifulSoup(html, parser), html

    def json(self, url: str, referer: str = "") -> Any:
        r = self.get(url, referer=referer)
        try:
            return r.json()
        finally:
            r.close()


try:
    import lxml  # noqa: F401

    _HAS_LXML = True
except ImportError:
    _HAS_LXML = False


# ----------------------------------------------------------------------------
# 规则
# ----------------------------------------------------------------------------


@dataclass
class Rule:
    name: str = "default"
    type: str = "html"  # html | json
    start_urls: list[str] = field(default_factory=list)
    page_start: int = 1
    page_end: int = 1

    # --- html 模式 ---
    list_item: str = ""  # 列表页里「图集链接」的选择器
    list_title: str = ""  # 相对 item 的标题选择器（可选）
    list_url_regex: str = ""  # 只保留 href 匹配该正则的条目（过滤栏目/标签页）
    next_page: str = ""  # 翻页链接选择器（可选，配合 max_pages）
    max_pages: int = 1

    detail_title: str = "h1, title"  # 详情页标题选择器
    detail_images: str = "img"  # 详情页图片选择器
    image_attrs: list[str] = field(default_factory=list)
    gallery_next: str = ""  # 图集内翻页（多图分页的站点）选择器

    # --- 图集内翻页（按 URL 模板，适合 ?page=N 或 /N/ 型站点）---
    gallery_page_url: str = ""  # 模板，含 {base}（详情页地址）与 {page}
    gallery_pages: int = 1  # 最多翻几页
    gallery_stop_empty: bool = True  # 某页没抓到新图就停（防无效页回落到第一页）

    # --- 地址改写 ---
    image_rewrite: list[list[str]] = field(default_factory=list)  # [[正则, 替换], ...]

    # --- json 模式 ---
    items_path: str = ""  # 列表接口里 items 的路径，如 data.list[*]
    item_url_field: str = "url"
    item_title_field: str = "title"
    images_path: str = ""  # 图片数组路径
    image_field: str = "url"

    # --- 通用选项 ---
    min_bytes: int = 8 * 1024  # 小于此体积视为缩略图/占位图，丢弃
    max_images: int = 0  # 单个图集最多下几张，0 = 不限
    exclude: list[str] = field(default_factory=list)  # 地址包含这些词则跳过
    include_only: list[str] = field(default_factory=list)  # 只保留包含这些词的地址
    referer: str = ""  # 下载图片时带的 Referer；留空则用详情页地址

    @staticmethod
    def load(path: str | Path) -> "Rule":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f for f in Rule.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(raw) - known
        if unknown:
            log(f"⚠️  规则里有未识别的字段（已忽略）：{', '.join(sorted(unknown))}")
        return Rule(**{k: v for k, v in raw.items() if k in known})

def build_page_urls(rule: Rule) -> list[str]:
    urls: list[str] = []
    for page in range(rule.page_start, rule.page_end + 1):
        for u in rule.start_urls:
            if "{page}" in u:
                urls.append(u.replace("{page}", str(page)))
            elif page == rule.page_start:
                urls.append(u)
    # 去重保序
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


# ----------------------------------------------------------------------------
# 抓取阶段
# ----------------------------------------------------------------------------


def extract_links_html(f: Fetcher, rule: Rule) -> list[dict[str, str]]:
    """阶段一：从列表页收集图集链接。"""
    galleries: list[dict[str, str]] = []
    seen: set[str] = set()

    def push(url: str, title: str) -> None:
        key = url_key(url)
        if key in seen:
            return
        seen.add(key)
        galleries.append({"url": url, "title": title})

    for url in build_page_urls(rule):
        try:
            soup, _ = f.soup(url)
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ 列表页失败 {url} —— {e}")
            continue

        if rule.list_item:
            nodes = soup.select(rule.list_item)
            log(f"  · {url} → 命中 {len(nodes)} 个条目")
            for n in nodes:
                a = n if n.name == "a" else n.select_one("a[href]")
                if a is None:
                    continue
                href = abs_url(url, a.get("href", ""))
                if not href:
                    continue
                if rule.list_url_regex and not re.search(rule.list_url_regex, href):
                    continue
                title = ""
                if rule.list_title:
                    t = n.select_one(rule.list_title)
                    title = t.get_text(" ", strip=True) if t else ""
                if not title:
                    title = best_title(n, a, rule.list_title)
                push(href, title)

            # 翻页
            if rule.next_page and rule.max_pages > 1:
                cur, page = soup, 1
                while page < rule.max_pages:
                    nxt = cur.select_one(rule.next_page)
                    href = abs_url(url, nxt.get("href", "")) if nxt else ""
                    if not href or url_key(href) in seen:
                        break
                    page += 1
                    try:
                        cur, _ = f.soup(href)
                    except Exception as e:  # noqa: BLE001
                        log(f"  ✗ 翻页失败 {href} —— {e}")
                        break
                    nodes = cur.select(rule.list_item)
                    log(f"  · {href} → 命中 {len(nodes)} 个条目")
                    for n in nodes:
                        a = n if n.name == "a" else n.select_one("a[href]")
                        if a is None:
                            continue
                        h = abs_url(href, a.get("href", ""))
                        if not h:
                            continue
                        if rule.list_url_regex and not re.search(rule.list_url_regex, h):
                            continue
                        push(h, best_title(n, a, rule.list_title))
        else:
            # 没给列表选择器：把 start_urls 本身当图集
            push(url, "")
            break

    return galleries


def extract_links_json(f: Fetcher, rule: Rule) -> list[dict[str, str]]:
    galleries: list[dict[str, str]] = []
    seen: set[str] = set()
    for url in build_page_urls(rule):
        try:
            data = f.json(url)
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ 接口失败 {url} —— {e}")
            continue
        items = dig(data, rule.items_path) if rule.items_path else [data]
        log(f"  · {url} → 命中 {len(items)} 个条目")
        for it in items:
            u = dig(it, rule.item_url_field)
            u = u[0] if u else ""
            if isinstance(u, dict):  # 有些站给 {url: xxx, width: ...}
                u = u.get("url", "")
            if not u:
                continue
            u = abs_url(url, str(u))
            if url_key(u) in seen:
                continue
            seen.add(url_key(u))
            t = dig(it, rule.item_title_field)
            galleries.append({"url": u, "title": str(t[0]) if t else ""})
    return galleries


def collect_image_urls(f: Fetcher, rule: Rule, page_url: str) -> list[str]:
    """阶段二：从详情页抽出图片地址（含图集内翻页）。"""
    urls: list[str] = []
    seen: set[str] = set()
    soup_cache: dict[str, BeautifulSoup] = {}

    def soup_of(u: str) -> BeautifulSoup:
        """同一页只解析一次（翻页时要用两次 soup 的时候省一次请求）。"""
        k = url_key(u)
        if k not in soup_cache:
            soup_cache[k] = f.soup(u)[0]
        return soup_cache[k]

    def keep(u: str) -> bool:
        if not u:
            return False
        low = u.lower()
        if JUNK_PAT.search(low):
            return False
        path = up.urlparse(low).path
        if os.path.splitext(path)[1] and os.path.splitext(path)[1] not in IMG_EXT:
            return False
        if rule.include_only and not any(k.lower() in low for k in rule.include_only):
            return False
        if rule.exclude and any(k.lower() in low for k in rule.exclude):
            return False
        return url_key(u) not in seen

    def harvest(cur_url: str) -> int:
        """抓一页，返回本页新增的图片数。"""
        before = len(urls)
        if rule.type == "json":
            data = f.json(cur_url)
            vals = dig(data, rule.images_path) if rule.images_path else []
            for v in vals:
                if isinstance(v, dict):
                    v = v.get(rule.image_field) or next(iter(v.values()), "")
                if isinstance(v, str):
                    u = apply_rewrites(abs_url(cur_url, v), rule.image_rewrite)
                    if keep(u):
                        seen.add(url_key(u))
                        urls.append(u)
        else:
            soup = soup_of(cur_url)
            for node in soup.select(rule.detail_images):
                u = apply_rewrites(abs_url(cur_url, pick_url(node, rule.image_attrs or None)),
                                   rule.image_rewrite)
                if keep(u):
                    seen.add(url_key(u))
                    urls.append(u)
        return len(urls) - before

    visited_pages: set[str] = set()

    if rule.gallery_page_url:
        # ---- 按 URL 模板翻页（?page=N 或 /N/ 型）----
        base = page_url
        total_pages = max(1, rule.gallery_pages)
        for page in range(1, total_pages + 1):
            cur = rule.gallery_page_url.replace("{base}", base).replace("{page}", str(page))
            if url_key(cur) in visited_pages:
                break
            visited_pages.add(url_key(cur))
            try:
                added = harvest(cur)
            except Exception as e:  # noqa: BLE001
                log(f"      · 图集第 {page} 页取不到（{type(e).__name__}: {e}）")
                break
            if added == 0 and rule.gallery_stop_empty and page > 1:
                break
            if rule.max_images and len(urls) >= rule.max_images:
                break
    else:
        # ---- 跟着「下一页」链接翻页 ----
        cur_url = page_url
        for _ in range(50):  # 图集内最多翻 50 页，防死循环
            if url_key(cur_url) in visited_pages:
                break
            visited_pages.add(url_key(cur_url))
            try:
                added = harvest(cur_url)
            except Exception as e:  # noqa: BLE001
                log(f"      · 图集页取不到（{type(e).__name__}: {e}）")
                break

            nxt = ""
            if rule.gallery_next:
                a = soup_of(cur_url).select_one(rule.gallery_next)
                nxt = abs_url(cur_url, a.get("href", "")) if a else ""
            if not nxt:
                break
            cur_url = nxt
            if rule.max_images and len(urls) >= rule.max_images:
                break

    if rule.max_images:
        urls = urls[: rule.max_images]
    return urls


def collect_from_auto(f: Fetcher, page_url: str) -> list[str]:
    """无规则模式：把页面上所有 <img>/<a 指向图片> 都收进来。"""
    soup, _ = f.soup(page_url)
    urls: list[str] = []
    seen: set[str] = set()
    for img in soup.find_all("img"):
        u = abs_url(page_url, pick_url(img))
        if u and not JUNK_PAT.search(u) and url_key(u) not in seen:
            seen.add(url_key(u))
            urls.append(u)
    for a in soup.find_all("a", href=True):
        u = abs_url(page_url, a["href"])
        ext = os.path.splitext(up.urlparse(u).path)[1].lower()
        if ext in IMG_EXT and url_key(u) not in seen:
            seen.add(url_key(u))
            urls.append(u)
    return urls


# ----------------------------------------------------------------------------
# 下载阶段
# ----------------------------------------------------------------------------


@dataclass
class Stats:
    total: int = 0
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, **kw: int) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)


def download_one(
    f: Fetcher,
    url: str,
    dest: Path,
    referer: str,
    stats: Stats,
    min_bytes: int,
    idx: int,
    total: int,
) -> None:
    if dest.exists() and dest.stat().st_size >= min_bytes:
        stats.add(skipped=1)
        log(f"    [{idx}/{total}] 已存在，跳过")
        return
    try:
        r = f.get(url, referer=referer, binary=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        size = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fh.write(chunk)
                    size += len(chunk)
        ct = r.headers.get("Content-Type", "")
        r.close()

        if size < min_bytes:
            tmp.unlink(missing_ok=True)
            stats.add(skipped=1)
            log(f"    [{idx}/{total}] 体积过小({size}B)，疑似缩略图 → 丢弃")
            return
        if ct and not ct.lower().startswith(("image/", "application/octet-stream")):
            tmp.unlink(missing_ok=True)
            stats.add(skipped=1)
            log(f"    [{idx}/{total}] 非图片({ct}) → 丢弃")
            return
        # 真正的扩展名可能与 URL 不一致，纠正一下
        real_ext = guess_ext(url, ct)
        final = dest.with_suffix(real_ext)
        if final != dest:
            final.unlink(missing_ok=True)
        tmp.replace(final)
        stats.add(ok=1, bytes=size)
        log(f"    [{idx}/{total}] ✓ {final.name}  {size/1024:.0f} KB")
    except Exception as e:  # noqa: BLE001
        stats.add(failed=1)
        log(f"    [{idx}/{total}] ✗ {e}")


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    proxies, no_env = resolve_proxy(args.proxy)
    f = Fetcher(
        proxy=proxies,
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
        ignore_robots=args.ignore_robots,
        no_env=no_env,
    )
    if proxies:
        log(f"🌐 走代理 {proxies}")
    elif no_env:
        log("🌐 直连（已忽略环境变量代理）")

    # ---- 组装图集清单 ----
    galleries: list[dict[str, str]] = []
    if args.rule:
        rule = Rule.load(args.rule)
        log(f"📋 规则：{rule.name}（{rule.type} 模式）")
        log("① 收集图集列表…")
        if args.urls:
            for u in args.urls:
                galleries.append({"url": u, "title": ""})
        elif rule.type == "json":
            galleries = extract_links_json(f, rule)
        else:
            galleries = extract_links_html(f, rule)
    elif args.from_file:
        rule = Rule(name="fromfile")
        for line in Path(args.from_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                galleries.append({"url": line, "title": ""})
    elif args.auto:
        rule = Rule(name="auto")
        galleries = [{"url": u, "title": ""} for u in args.auto]
    else:
        log("需要 --rule / --from-file / --auto 三者之一")
        return 2

    if not galleries:
        log("没有收集到任何图集，收工。")
        return 1

    log(f"\n📚 共 {len(galleries)} 个图集")

    if args.list_only:
        log("\n（--list-only：只列图集清单，不解析详情页）")
        for i, g in enumerate(galleries, 1):
            log(f"  {i:>4}. {g['title']}")
            log(f"        {g['url']}")
        return 0

    # ---- 逐图集抽出图片地址 ----
    log("\n② 解析图片地址…")
    jobs: list[tuple[dict[str, str], list[str]]] = []
    quota = getattr(args, "max_total", 0) or 0
    acc = 0
    for i, g in enumerate(galleries, 1):
        title = g["title"] or f"gallery_{i:03d}"
        try:
            if args.rule and rule.type == "html" and not rule.detail_images:
                imgs = collect_from_auto(f, g["url"])
            elif args.rule or args.from_file:
                imgs = collect_image_urls(f, rule if args.rule else Rule(name="default"), g["url"])
            else:
                imgs = collect_from_auto(f, g["url"])
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ [{i}/{len(galleries)}] {title} 解析失败：{e}")
            continue
        log(f"  · [{i}/{len(galleries)}] {title} → {len(imgs)} 张")
        if imgs:
            jobs.append(({**g, "title": title}, imgs))
            acc += len(imgs)
        # 够了就停手，不再白跑后面的详情页
        if quota and acc >= quota:
            log(f"  ⏹  已达 --max-total={quota}，停止解析（已解析 {i}/{len(galleries)} 个图集）")
            break

    # 全局总量上限：按图集顺序截断，保证「总共不超过 N 张」
    if getattr(args, "max_total", 0):
        kept: list[tuple[dict[str, str], list[str]]] = []
        acc = 0
        for g, imgs in jobs:
            if acc >= args.max_total:
                break
            room = args.max_total - acc
            if len(imgs) > room:
                imgs = imgs[:room]
            kept.append((g, imgs))
            acc += len(imgs)
        if len(kept) < len(jobs) or acc < sum(len(v) for _, v in jobs):
            log(f"✂️  --max-total={args.max_total}：图集 {len(jobs)}→{len(kept)}，图片截断到 {acc} 张")
        jobs = kept

    total_imgs = sum(len(v) for _, v in jobs)
    log(f"\n🖼️  合计 {total_imgs} 张图片")

    if args.dry_run:
        log("\n（--dry-run：仅列出，不下载）")
        for g, imgs in jobs:
            log(f"\n📁 {slugify(g['title'])}  ({len(imgs)} 张)")
            for u in imgs[: args.preview]:
                log(f"    {u}")
            if len(imgs) > args.preview:
                log(f"    … 另有 {len(imgs) - args.preview} 张")
        return 0

    if total_imgs == 0:
        log("没有可下载的图片。可以先用 `probe` 子命令看看页面上有哪些图。")
        return 1

    # ---- 下载 ----
    log(f"\n③ 开始下载（{args.workers} 线程，输出到 {Path(args.out).resolve()}）")
    out_root = Path(args.out)
    stats = Stats()
    manifest: list[dict[str, Any]] = []

    for gi, (g, imgs) in enumerate(jobs, 1):
        folder = out_root / slugify(g["title"], f"gallery_{gi:03d}")
        folder.mkdir(parents=True, exist_ok=True)
        log(f"\n📁 [{gi}/{len(jobs)}] {g['title']}  →  {folder}")
        referer = g["url"]
        n = len(imgs)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = []
            for k, u in enumerate(imgs, 1):
                ext = guess_ext(u)
                dest = folder / f"{k:03d}{ext}"
                futs.append(
                    pool.submit(
                        download_one, f, u, dest, referer, stats, args.min_bytes, k, n
                    )
                )
            for _ in as_completed(futs):
                pass

        # 每个图集写一份 meta，方便以后查来源
        meta = {
            "title": g["title"],
            "source": g["url"],
            "images": imgs,
            "count": n,
            "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (folder / "_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest.append({"title": g["title"], "path": str(folder), "count": n, "source": g["url"]})

    (out_root / "index.json").write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "galleries": manifest,
                "images_ok": stats.ok,
                "images_failed": stats.failed,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log("\n" + "=" * 56)
    log(f"✅ 完成：成功 {stats.ok} / 跳过 {stats.skipped} / 失败 {stats.failed}")
    log(f"   共下载 {stats.bytes/1048576:.1f} MB，HTTP 请求 {f.stats['requests']} 次，重试 {f.stats['errors']} 次")
    log(f"   输出目录：{out_root.resolve()}")
    log("=" * 56)
    return 0


# ----------------------------------------------------------------------------
# probe：帮你写规则
# ----------------------------------------------------------------------------


def probe(args: argparse.Namespace) -> int:
    proxy, no_env = resolve_proxy(args.proxy)
    f = Fetcher(proxy=proxy, timeout=args.timeout, retries=2,
                ignore_robots=args.ignore_robots, no_env=no_env)

    log(f"🔍 探测 {args.url}\n")
    soup, html = f.soup(args.url)
    log(f"页面大小 {len(html)} 字符，<title> = {soup.title.get_text(strip=True) if soup.title else '(无)'}\n")

    imgs = soup.find_all("img")
    log(f"── <img> 共 {len(imgs)} 个 ──")
    for i, img in enumerate(imgs[: args.limit], 1):
        u = abs_url(args.url, pick_url(img))
        w, h = img.get("width", ""), img.get("height", "")
        cls = ".".join(img.get("class", [])[:3])
        log(f"  {i:>3}. {u}")
        log(f"       属性: {dict(list(img.attrs.items())[:5])}  尺寸:{w}x{h}  class:{cls}")

    # 给出可直接用的选择器建议
    log("\n── 选择器建议（按 class 统计）──")
    from collections import Counter

    cnt: Counter[str] = Counter()
    for img in imgs:
        for c in img.get("class", []) or []:
            cnt[f"img.{c}"] += 1
        parent = img.parent
        for c in (parent.get("class", []) or []) if parent else []:
            cnt[f"{parent.name}.{c} img"] += 1
    for sel, n in cnt.most_common(12):
        log(f"  {n:>4} 次   {sel}")

    links = [a for a in soup.find_all("a", href=True)]
    log(f"\n── <a> 共 {len(links)} 个（前 {args.limit}）──")
    for i, a in enumerate(links[: args.limit], 1):
        txt = a.get_text(" ", strip=True)[:40]
        log(f"  {i:>3}. {abs_url(args.url, a['href'])}   「{txt}」")

    log("\n── <a> 选择器建议 ──")
    cnt2: Counter[str] = Counter()
    for a in links:
        for c in a.get("class", []) or []:
            cnt2[f"a.{c}"] += 1
        parent = a.parent
        for c in (parent.get("class", []) or []) if parent else []:
            cnt2[f"{parent.name}.{c} a"] += 1
    for sel, n in cnt2.most_common(12):
        log(f"  {n:>4} 次   {sel}")

    log("\n💡 把这些选择器填进 rules/<你的站点>.json 就能跑了（参考 rules/example.json）")
    return 0


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="crawl.py",
        description="图集/写真类站点通用爬虫（规则驱动）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--proxy", default=os.environ.get("PHOTOCRAWL_PROXY", ""),
                        help="代理：local=127.0.0.1:7890；none=真直连（忽略环境变量代理）；或直接写地址 http://ip:port")
        sp.add_argument("--timeout", type=int, default=20, help="单请求超时秒数（默认 20）")
        sp.add_argument("--retries", type=int, default=3, help="失败重试次数（默认 3）")
        sp.add_argument("--delay", type=float, default=0.0, help="每次请求后额外等待秒数（礼貌抓取）")
        sp.add_argument("--ignore-robots", action="store_true", help="忽略 robots.txt（自行确认合规性）")

    r = sub.add_parser("run", help="抓取并下载")
    common(r)
    r.add_argument("--rule", help="规则文件 rules/*.json")
    r.add_argument("--from-file", help="从文本文件读取图集地址（一行一个，# 开头为注释）")
    r.add_argument("--auto", nargs="+", metavar="URL", help="无规则：直接给出详情页地址")
    r.add_argument("--urls", nargs="+", metavar="URL", help="配合 --rule：只抓这些详情页")
    r.add_argument("--out", default="./downloads", help="输出目录（默认 ./downloads）")
    r.add_argument("--workers", type=int, default=8, help="下载并发数（默认 8）")
    r.add_argument("--min-bytes", type=int, default=8 * 1024, help="小于该体积的图片丢弃（默认 8KB）")
    r.add_argument("--max-total", type=int, default=0, help="本次最多下载多少张（0 = 不限），按图集顺序截断")
    r.add_argument("--list-only", action="store_true", help="只收集并打印图集清单，不解析详情页（调规则用）")
    r.add_argument("--dry-run", action="store_true", help="只列出将下载的内容，不落盘")
    r.add_argument("--preview", type=int, default=5, help="dry-run 时每个图集预览几条")

    pb = sub.add_parser("probe", help="探测页面结构，辅助编写规则")
    common(pb)
    pb.add_argument("url")
    pb.add_argument("--limit", type=int, default=25, help="每类最多列几条")

    args = p.parse_args(argv)
    try:
        return run(args) if args.cmd == "run" else probe(args)
    except KeyboardInterrupt:
        log("\n已中断（已下载的文件保留，重跑会自动跳过）")
        return 130


if __name__ == "__main__":
    sys.exit(main())
