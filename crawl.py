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

 ---------------------------------------------------------------------------
 📖 阅读指南（配合 docs/TUTORIAL.md 一起看效果最好）
 ---------------------------------------------------------------------------
 全文按功能分 7 大块，搜索对应的分隔线注释即可跳转：
   1. 常量与工具函数   —— 纯函数，无状态，最容易读懂，建议从这里入门
   2. Fetcher 网络层   —— 怎么"礼貌地"发请求（伪装、重试、代理、robots）
   3. Rule 规则        —— 怎么把"某个站怎么爬"变成一份 JSON 配置
   4. 抓取阶段          —— 列表页收集链接 + 详情页抽图片地址
   5. 下载阶段          —— 多线程下载单个文件的全部防御措施
   6. 主流程 run       —— 把上面所有零件串成流水线
   7. probe / CLI      —— 命令行入口和调规则用的探测工具

 语法注释约定：🆕 标记 = Python 语法点解释；💡 标记 = 为什么这么设计。
 已经会 Python 的可以只看 💡 的部分。
 ---------------------------------------------------------------------------
"""

from __future__ import annotations

# ============================================================================
# import 区 —— 全部是标准库 + 2 个第三方库，逐个说明是干嘛的
# ============================================================================

import argparse          # 命令行参数解析（标准库）：--proxy、--workers 这些选项靠它
import hashlib           # 哈希摘要（标准库）：可用于文件去重/校验（本项目暂未用到，保留）
import json              # JSON 读写（标准库）：加载 rules/*.json、写 _meta.json
import os                # 操作系统接口（标准库）：路径拆分、环境变量
import random            # 随机数（标准库）：给重试等待加随机抖动，避免"整点重试风暴"
import re                # 正则表达式（标准库）：JUNK 黑名单过滤、文件名清洗
import sys               # 系统交互（标准库）：sys.exit() 带报错信息退出
import threading         # 多线程（标准库）：threading.local、threading.Lock
import time              # 时间（标准库）：sleep 限速、时间戳
import urllib.parse as up            # URL 解析/拼接（标准库）：urljoin、urlparse
import urllib.robotparser as robotparser  # robots.txt 解析器（标准库，合规用）
from concurrent.futures import ThreadPoolExecutor, as_completed
    # 🆕 concurrent.futures 是标准库的"线程池/进程池"封装：
    #    ThreadPoolExecutor(n) = 开一个最多 n 个线程的池子
    #    as_completed(futs)    = 谁先干完谁先出来（收割结果的顺序=完成顺序）
from dataclasses import dataclass, field
    # 🆕 @dataclass 装饰器：自动帮你生成 __init__/__repr__/__eq__
    #    只需要声明字段，不用手写一大坨 self.xxx = xxx
    #    field(default_factory=list) = "每个实例给一个新列表"
    #    （直接写 =[] 会踩经典大坑：所有实例共享同一个列表）
from pathlib import Path
    # 🆕 面向对象的路径操作（标准库）：
    #    Path("a") / "b" / "c.jpg" 代替 os.path.join("a", "b", "c.jpg")
    #    .exists() .stat() .mkdir() .with_suffix() 都是它的方法
from typing import Any, Iterable, Iterator
    # 🆕 类型标注辅助：Any=任意类型，Iterable=可迭代，Iterator=迭代器
    #    只影响 IDE 提示和类型检查器，运行时不生效，相当于注释

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 requests，请先安装：pip install requests beautifulsoup4 lxml")

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 beautifulsoup4，请先安装：pip install requests beautifulsoup4 lxml")

# ----------------------------------------------------------------------------
# 常量 —— 全局配置放最前面，改行为先看这里
# ----------------------------------------------------------------------------

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# 💡 User-Agent 是每个 HTTP 请求头里的"自我介绍"。
#    requests 的默认值是 "python-requests/2.x"，网站一看就知道是脚本，
#    很多站直接回 403。伪装成 Chrome 浏览器是最基本的礼貌（也是基本生存技能）。

# 常见的懒加载属性，按优先级尝试。
# 💡 背景：现代网站为了首屏速度，<img> 的 src 里先放一个 1 像素占位 GIF，
#    真图地址藏在 data-xxx 属性里，等用户滚动到眼前才用 JS 换进去。
#    爬虫没有浏览器、JS 永远不执行，所以必须主动去这些属性里找真图。
#    这个列表按"出现频率从高到低"排列，最后才兜底到 src。
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

# 算"正经图片"的扩展名集合（set 查找是 O(1)，比 list 快）
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".jfif"}

# 明显不是正文图的路径关键词（头像、图标、广告位等）。
# 💡 re.I = 忽略大小写（Logo/logo/LOGO 都算）。
#    ⚠️ 血泪教训：`ads?/` 必须带 `(?:^|/)` 左边界！
#    否则 "...uplo**ads**/xxx.webp" 文件名里含 ads 的正图也会被误杀，
#    而且是"静默少爬"——不报任何错，一整站图莫名消失。
#    写黑名单正则时永远要问自己：这个词会出现在 URL 的哪个位置？
# 注意：ads?/ 必须带路径边界，否则会把 uploads/ 里的 "ads/" 误判成广告目录
JUNK_PAT = re.compile(
    r"(avatar|logo|icon|sprite|placeholder|loading|blank|spacer|pixel|"
    r"advert|(?:^|/)ads?/|banner|button|emoji|favicon|thumb[-_]?icon)",
    re.I,
)

# Windows 文件名的非法字符：<>:"/\|?* 和所有控制字符（\x00-\x1f）。
# 💡 \x00-\x1f 是 ASCII 的 0~31 号不可见字符，写进文件名会出诡异问题
ILLEGAL_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def log(msg: str) -> None:
    # 🆕 -> None 是返回值类型标注：这个函数不返回东西，只打印。
    # flush=True 强制立即输出：Python 的 print 默认攒一批再输出（缓冲），
    # 长时间跑批时如果不 flush，日志会"憋着"半天不显示，看不出进度。
    print(msg, flush=True)


def resolve_proxy(value: str) -> tuple[str | None, bool]:
    """
    归一化 --proxy 的取值，返回 (代理地址, 是否忽略环境变量代理)。

    🆕 语法点：
      - tuple[str | None, bool] 是返回值类型：一个二元组，
        第一项可能是字符串或 None（str | None 是 3.10+ 的联合类型写法，
        等价于老写法 Optional[str]）
      - 函数把"人话参数"翻译成"机器参数"，是典型的适配器写法

      local / on / default  → 本地 127.0.0.1:7890
      none / off / direct / 0 → 真·直连（同时忽略环境变量里的 HTTP(S)_PROXY）
      其它非空字符串         → 当作代理地址
      空                    → 交给环境变量
    """
    # 🆕 (value or "") 的惯用法：value 是 None 时退化成 ""，防止 .strip() 报错
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
# 工具函数 —— 每个都很小、只干一件事，是读代码的最佳起点
# ----------------------------------------------------------------------------


def abs_url(base: str, href: str) -> str:
    """把相对地址补成绝对地址；顺便去掉 fragment。

    HTML 里的地址有四种形态，都要能处理：
      https://cdn.site.com/a.jpg   绝对地址 → 原样返回
      //cdn.site.com/a.jpg         协议相对 → 补上 http:/https:
      /img/2026/a.jpg              根相对   → 挂到当前域名下
      javascript:void(0)           垃圾     → 返回空字符串
    💡 永远不要手写字符串拼接来拼 URL，urljoin 处理过你没想到的所有边界。
    """
    if not href:
        return ""
    href = href.strip()
    # 💡 startswith 接元组 = "命中任意一个就算"，比写 4 个 or 简洁
    if href.startswith(("data:", "javascript:", "about:", "#")):
        return ""
    # 协议相对：//cdn.example.com/a.jpg
    if href.startswith("//"):
        scheme = up.urlparse(base).scheme or "https"
        href = f"{scheme}:{href}"
    out = up.urljoin(base, href)
    # 💡 split("#", 1)[0]：# 后面是页内锚点，不是资源的一部分，去掉防重复
    return out.split("#", 1)[0]


def first_srcset(value: str) -> str:
    """从 srcset 里挑第一个候选地址。

    🆕 背景：srcset 是 HTML 的"多分辨率"语法，
      <img srcset="small.jpg 300w, large.jpg 1200w">
    逗号分隔多个候选。爬虫没有浏览器，分辨率选不了，
    简单取第一个（通常是缩略图，但至少是个真图地址）。
    """
    if not value:
        return ""
    # 🆕 链式调用拆开看：
    #   value.split(",")[0]        → 取第一个候选 "small.jpg 300w"
    #   .strip().split(" ")[0]     → 去空格再砍掉宽度描述 → "small.jpg"
    return value.split(",")[0].strip().split(" ")[0]


def pick_url(tag, attrs: Iterable[str] | None = None) -> str:
    """按优先级从标签里取出图片地址，兼顾懒加载与 srcset。

    🆕 语法点：参数 attrs 的类型是 Iterable[str] | None，
      默认值 None 表示"调用方没给就用内置的 LAZY_ATTRS"。
    💡 这是"策略带默认值"的经典写法：普通站走默认顺序，
       特殊站可在规则 JSON 里用 image_attrs 字段覆盖。
    """
    for a in (attrs or LAZY_ATTRS):
        # 🆕 tag 是 BeautifulSoup 的 Tag 对象，tag.get(a) 相当于
        #    字典取值：属性存在返回值，不存在返回 None（不会 KeyError）
        v = tag.get(a)
        if v and isinstance(v, str) and v.strip():
            return v.strip()      # 第一个有值的属性就是真图
    # <picture><source srcset=...>
    # 💡 有些新站把图包在 <picture> 里，真正的地址在 <source> 子标签上
    for src in tag.select("source[srcset]"):
        v = first_srcset(src.get("srcset", ""))
        if v:
            return v
    v = tag.get("srcset")
    if v:
        return first_srcset(v)
    return ""


def slugify(text: str, fallback: str = "item", maxlen: int = 80) -> str:
    """把标题变成安全的文件名/目录名。

    💡 标题里常有 Windows 禁止的字符（:?*| 之类）和超长文本，
       不清洗的话建目录会直接报错。步骤：空白→下划线 → 删非法字符
       → 去首尾的句点空格 → 空了给兜底名 → 截断到 maxlen。
    """
    text = (text or "").strip()
    # 🆕 re.sub(模式, 替换, 原文)：r"\s+" 匹配任意连续空白（含换行制表符）
    text = re.sub(r"\s+", "_", text)
    text = ILLEGAL_FS.sub("", text)
    text = text.strip("._ ")
    if not text:
        text = fallback
    return text[:maxlen]


def url_key(url: str) -> str:
    """用于去重的规范化地址：去掉 query（很多站用 ?w=300 生成缩略图）。

    💡 去重为什么不能直接比原始 URL？因为同一个图可能出现成：
         a.jpg         a.jpg?v=2         a.jpg?w=1200
       去掉 ? 后面的部分再比，三者就是同一个 key，只下一次。
    """
    p = up.urlparse(url)
    # 💡 f-string 里直接取 urlparse 结果的属性，拼回"协议://域名+路径"
    return f"{p.scheme}://{p.netloc}{p.path}"


def apply_rewrites(url: str, rules: list | None) -> str:
    """按 [[正则, 替换], ...] 依次改写图片地址（如缩略图→原图）。

    💡 有些站的图片 URL 有固定规律：
         thumb/abc.jpg →  把 /thumb/ 删掉就是原图
       规则 JSON 里写 "image_rewrite": [["/thumb/", "/"]] 即可，
       不用改一行 Python 代码——这就是规则驱动的灵活性。
    """
    if not url or not rules:
        return url
    for pair in rules:
        # 🆕 isinstance(pair, (list, tuple))：防御性检查，配置写错了别崩
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        try:
            url = re.sub(str(pair[0]), str(pair[1]), url)
        except re.error as e:  # noqa: PERF203
            # 💡 re.error = 正则本身语法错误。配置是用户写的，
            #    坏配置应该警告而不是让整个程序崩溃
            log(f"⚠️  image_rewrite 正则无效：{pair[0]}（{e}）")
    return url


def best_title(node, a, title_sel: str = "", maxlen: int = 120) -> str:
    """
    尽力取到「最完整」的标题。

    很多站点列表页的链接文字被服务器截断成 "XXX: A Story in ..."，
    真正的完整标题放在 `title` 属性或 `img[alt]` 里。这里把所有候选
    收集起来，优先选不以省略号结尾的、且最长的那个。

    💡 设计思想：不试图"修复截断的字符串"（那是在猜），
       而是"收集所有候选、用启发式规则挑最好的一个"（这是在选择）。
       后者明显更可靠。

    🆕 语法点：node 和 a 是 BeautifulSoup 的 Tag 对象；
       select_one() 返回第一个匹配 CSS 选择器的元素，找不到返回 None。
    """
    cands: list[str] = []
    if title_sel:
        t = node.select_one(title_sel)
        if t:
            # 🆕 get_text(" ", strip=True)：取标签内所有文字，
            #    子标签之间用空格连接，并去掉首尾空白
            cands.append(t.get_text(" ", strip=True))
    attr_title = a.get("title") or ""
    if attr_title:
        cands.append(attr_title)
    cands.append(node.get_text(" ", strip=True))
    for scope in (node, a):
        # 🆕 hasattr(scope, "select_one")：防御检查（a 可能是普通对象）
        img = scope.select_one("img[alt]") if hasattr(scope, "select_one") else None
        if img and img.get("alt"):
            cands.append(img["alt"].strip())

    # 🆕 列表推导式：把空候选过滤掉，等价于一个 for 循环 + if
    cands = [c for c in cands if c]
    if not cands:
        return ""
    # 优先在"完整候选"里挑；全被截断了才退回原候选池
    complete = [c for c in cands if not c.endswith(("...", "…"))]
    pool = complete or cands
    # 🆕 max(pool, key=len)：按"字符串长度"取最大者，即最长的那个
    return max(pool, key=len)[:maxlen]


def guess_ext(url: str, content_type: str = "") -> str:
    """猜文件真实扩展名：先看 URL 路径，再看 HTTP 的 Content-Type。

    💡 为什么需要猜？两种常见错位：
       1. URL 写着 .jpg，服务器实际回的是 webp（省流量）
       2. URL 根本没有扩展名（如 /image/12345），只能靠 Content-Type
       扩展名错了双击打不开，用户体验直接归零，所以值得猜。
    """
    ext = os.path.splitext(up.urlparse(url).path)[1].lower()
    if ext in IMG_EXT:
        return ".jpg" if ext == ".jfif" else ext
    ct = (content_type or "").lower()
    # 💡 用 "in" 而不是 == 判断：Content-Type 可能是 "image/webp; charset=..."
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

    💡 这是给 json 模式用的"迷你 JSONPath"。接口返回的数据藏得深：
         {"data": {"list": [{"url": "..."}, ...]}}
       用路径字符串 "data.list[*].url" 就能取到，不用一层层写嵌套取值。

    🆕 语法点：Any 表示"什么类型都行"——JSON 解析出来就是
       dict/list/str/int/bool/None 的大杂烩，只能用 Any。
    """
    # cur 是"当前层级的所有命中节点"，每处理一段路径就往下钻一层
    cur: list[Any] = [obj]
    for part in [p for p in path.split(".") if p != ""]:
        # 🆕 str.endswith("[*]")：a[*] 表示"a 是数组，把每个元素都展开"
        star = part.endswith("[*]")
        key = part[:-3] if star else part
        nxt: list[Any] = []
        for c in cur:
            v: Any = c
            if key:
                if isinstance(v, dict):
                    v = v.get(key)
                elif isinstance(v, list) and key.lstrip("-").isdigit():
                    # 🆕 "data.0.url" 里的 0 是数组下标；
                    #    lstrip("-") 兼容 -1 这种倒数下标
                    i = int(key)
                    v = v[i] if -len(v) <= i < len(v) else None
                else:
                    v = None
            if star:
                if isinstance(v, list):
                    # 🆕 extend vs append：extend 把列表"摊平"逐个放进去
                    nxt.extend(v)
            elif v is not None:
                nxt.append(v)
        cur = nxt
        if not cur:
            break
    return cur


# ----------------------------------------------------------------------------
# 网络层 Fetcher —— 全部 HTTP 请求从这里出去，方便统一加伪装/重试/合规
# ----------------------------------------------------------------------------


@dataclass
class Fetcher:
    """HTTP 请求器：发请求的唯一入口。

    🆕 @dataclass 语法详解：下面每个"字段 = 默认值"的行，都会被
       自动转成 __init__ 的参数，即：
         Fetcher(proxy="http://...", timeout=30)
       等价于手写：
         def __init__(self, proxy=None, timeout=20, retries=3, ...):
             self.proxy = proxy
             self.timeout = timeout
             ...
       8 个字段省下 20 行样板代码，还免费获得漂亮的 repr 和 == 比较。

    💡 把所有请求行为收拢到一个类里，好处是：想改 UA、加 Cookie、
       换重试策略，只动这一个文件，别处全部自动生效。
    """

    proxy: str | None = None        # 🆕 str | None = "要么是字符串要么是 None"
    timeout: int = 20               # 单请求超时（秒）。⚠️ 不设超时 = 可能永远卡住
    retries: int = 3                # 失败重试次数
    delay: float = 0.0              # 每次成功请求后额外等待（秒），礼貌抓取用
    verify_robots: bool = True      # 是否检查 robots.txt（默认检查，合规）
    ignore_robots: bool = False     # 显式忽略 robots（需要人自己承担合规责任）
    no_env: bool = False  # True = 忽略环境变量里的 HTTP(S)_PROXY，真·直连

    def __post_init__(self) -> None:
        # 🆕 __post_init__ 是 dataclass 的钩子：__init__ 跑完后自动调用，
        #    适合做"字段初始化之后"的准备工作（初始化非常量属性）。
        # 💡 为什么不在字段声明里写？因为 threading.local() 这种对象
        #    不能作为 dataclass 字段默认值（会被所有实例共享，出并发 bug）
        self._local = threading.local()
        # 💡 robots.txt 每个域名只有一个，缓存起来避免反复下载；
        #    dict 当缓存 + Lock 保护 = 简易线程安全缓存
        self._robot_cache: dict[str, robotparser.RobotFileParser | None] = {}
        self._robot_lock = threading.Lock()
        self.stats = {"requests": 0, "errors": 0}

    # -- session per thread -------------------------------------------------
    @property
    def session(self) -> requests.Session:
        """每个线程一份独立的 requests.Session。

        🆕 @property 装饰器：把方法伪装成属性——写 f.session 就自动调用
           这个函数，不用加括号。适合"第一次访问时才创建"的懒加载场景。

        💡 为什么要每线程一份？requests.Session 内部不是线程安全的：
           10 个下载线程共用一个 Session 会出现连接状态错乱、
           Cookie 互相污染等"玄学 bug"。threading.local() 的特性是：
           每个线程看到的是自己独立的存储空间，天然隔离。
           这是多线程爬虫的标准写法，值得背下来。
        """
        # 🆕 getattr(obj, "s", None) = 取属性，没有就给 None（不抛异常）
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update(
                {
                    # 💡 Session.headers 对本 Session 的所有请求生效，
                    #    不用每个 get() 都传一遍
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
                # 💡 本项目实战踩过的最阴的坑：--proxy 写了却"像没生效"，
                #    报错反而是目标站的 SSL 错——元凶就是环境变量里藏着一个
                #    沙箱代理。trust_env=False = "别自作主张读环境变量"。
                s.trust_env = False
            elif self.no_env:
                # 💡 用户要"真直连"时也关掉 trust_env，
                #    否则环境代理又会在背后偷偷接管流量
                s.trust_env = False
            self._local.s = s
        return s

    # -- robots -------------------------------------------------------------
    def robots_allows(self, url: str) -> bool:
        """查 robots.txt 判断这个 URL 能不能抓。

        💡 robots.txt 是网站根目录的"抓取须知"（如 /admin/ 禁止爬）。
           遵守它是爬虫的底线，也是法律上的"善意"证据。
           ⚠️ 401/403 处理：robots 文件本身拒绝访问时，
           行业惯例视为"无特殊限制"（文件都看不了，何谈遵守）。
        """
        if not self.verify_robots or self.ignore_robots:
            return True
        parts = up.urlparse(url)
        root = f"{parts.scheme}://{parts.netloc}"
        # 🆕 with 锁对象：进入代码块自动加锁，退出自动解锁（异常也会解锁），
        #    等价于 手动 lock.acquire() ... lock.release() 但不会忘记释放
        with self._robot_lock:
            if root in self._robot_cache:
                rp = self._robot_cache[root]
            else:
                rp = None
                try:
                    r = self.session.get(root + "/robots.txt", timeout=self.timeout)
                    if r.status_code == 200:
                        # 🆕 RobotFileParser 是标准库自带的 robots 解析器，
                        #    parse() 喂给它文本行，can_fetch() 就能查询
                        rp = robotparser.RobotFileParser()
                        rp.parse(r.text.splitlines())
                    elif r.status_code in (401, 403):
                        rp = None  # 拒绝访问 robots，视为无限制
                except Exception:
                    rp = None  # robots 拿不到（网络问题）也别挡住正常抓取
                self._robot_cache[root] = rp
        # ⚠️ can_fetch 的查询放在锁外：读缓存不需要锁（GIL 保证字典读安全），
        #    缩小临界区 = 减少线程排队
        if rp is None:
            return True
        return rp.can_fetch(UA, url)

    # -- get ----------------------------------------------------------------
    def get(self, url: str, referer: str = "", binary: bool = False):
        """所有 GET 请求的唯一入口：合规检查 → 带重试地发 → 返回响应。

        🆕 语法点：
          - referer: str = ""      带默认值的关键字参数
          - binary: bool = False   True 时用流式下载（大文件不撑爆内存）
          - last: Exception | None 异常也可以当变量存，最后拼进报错信息
        """
        if not self.robots_allows(url):
            # 🆕 raise 自定义异常：PermissionError 是内置异常类型，
            #    语义就是"没权限"，调用方一眼看懂
            raise PermissionError(f"robots.txt 不允许抓取：{url}")
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                headers = {}
                if referer:
                    # 💡 Referer 防盗链的核心：图床检查"你从哪个页面跳来的"，
                    #    不是自家域名就拒绝。带上详情页地址即可绕过普通防盗链。
                    headers["Referer"] = referer
                r = self.session.get(
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    # 💡 stream=True：响应体不立刻下载，等调用方
                    #    iter_content() 一块一块取——下载大图必备
                    stream=binary,
                    allow_redirects=True,
                )
                self.stats["requests"] += 1
                if r.status_code == 404:
                    r.close()
                    # 💡 404 单独抛 FileNotFoundError 且"不重试"——
                    #    页面不存在，重试一万次也是 404，纯浪费
                    raise FileNotFoundError(f"404 {url}")
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code} {url}")
                if self.delay:
                    # 💡 基础延时 + 随机抖动：抖动让多个线程的请求
                    #    "错开"而不是整整齐齐齐射，更像真人
                    time.sleep(self.delay + random.uniform(0, self.delay * 0.5))
                return r
            except FileNotFoundError:
                # 🆕 裸 except + raise = "原样上抛，不做任何处理"，
                #    用于从宽泛的 except Exception 里豁免特定异常
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                self.stats["errors"] += 1
                if attempt < self.retries:
                    # 💡 指数退避：等 0.6*2^attempt 秒（1.2s→2.4s→4.8s...）
                    #    网站刚 502，0 秒后重试只会再吃一个 502；
                    #    min(..., 8) 封顶 8 秒；再叠一点随机抖动
                    time.sleep(min(2**attempt * 0.6, 8) + random.uniform(0, 0.4))
        raise RuntimeError(f"多次重试仍失败：{url}（{last}）")

    def soup(self, url: str, referer: str = "") -> tuple[BeautifulSoup, str]:
        """取网页并解析成 BeautifulSoup 对象。

        🆕 -> tuple[BeautifulSoup, str]：返回一个二元组（soup 对象, 原始 HTML）。
           原始 HTML 也一起返回，是因为 probe 命令要统计页面大小，省一次请求。
        """
        r = self.get(url, referer=referer)
        # 💡 apparent_encoding：requests 默认按 HTTP 头猜编码，很多中文站
        #    头里不写，会猜成 latin-1 导致乱码；apparent_encoding 按
        #    页面内容猜，准得多
        r.encoding = r.apparent_encoding or r.encoding
        html = r.text
        r.close()
        parser = "lxml" if _HAS_LXML else "html.parser"
        return BeautifulSoup(html, parser), html

    def json(self, url: str, referer: str = "") -> Any:
        """取 JSON 接口（json 模式的规则用这个）。

        🆕 try/finally：finally 块无论成功失败都执行——
           这里保证响应连接一定被关闭，哪怕 .json() 解析失败
        """
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
# 规则 Rule —— "某个站怎么爬"的全部知识，都以数据形式存在 JSON 里
# ----------------------------------------------------------------------------


@dataclass
class Rule:
    """一份站点规则 = 引擎眼里"这个站长什么样"的完整描述。

    💡 为什么要规则系统？10 个站 10 种结构，如果全写成
         if site == "taotu": ... elif site == "4kup": ...
       代码会迅速烂掉。规则驱动 = 代码只认"字段"，站与站的差异全在 JSON。
       换站 = 新增一个 JSON 文件，Python 零改动。

    字段分 6 组，每组之间用注释隔开，对照 rules/*.json 食用：
    """

    name: str = "default"
    type: str = "html"  # html | json
    # --- 入口与列表页翻页：{page} 占位符会被 page_start~page_end 展开 ---
    start_urls: list[str] = field(default_factory=list)
    # 🆕 field(default_factory=list)：可变默认值必须这么写。
    #    直接写 = [] 的话所有 Rule 实例共享同一个列表（经典大坑）
    page_start: int = 1
    page_end: int = 1

    # --- html 模式：列表页怎么解析 ---
    list_item: str = ""  # 列表页里「图集链接」的选择器
    list_title: str = ""  # 相对 item 的标题选择器（可选）
    list_url_regex: str = ""  # 只保留 href 匹配该正则的条目（过滤栏目/标签页）
    next_page: str = ""  # 翻页链接选择器（可选，配合 max_pages）
    max_pages: int = 1

    # --- html 模式：详情页怎么解析 ---
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

    # --- json 模式：接口返回 JSON 时的取值路径 ---
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
        """从 JSON 文件加载规则。

        🆕 @staticmethod：不需要实例就能调用的函数（Rule.load("x.json")）。
           -> "Rule" 带引号：类还没定义完，只能用字符串前向引用。

        💡 防御重点：JSON 里的字段名是用户手打的，写错很正常。
           用 Rule.__dataclass_fields__（dataclass 自动生成的字段字典）
           对比一遍，未知字段警告而不是静默忽略或直接崩溃。
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f for f in Rule.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(raw) - known
        if unknown:
            log(f"⚠️  规则里有未识别的字段（已忽略）：{', '.join(sorted(unknown))}")
        # 🆕 字典推导式 + 解包：**{k: v ...} 把过滤后的字典展开成关键字参数
        return Rule(**{k: v for k, v in raw.items() if k in known})

def build_page_urls(rule: Rule) -> list[str]:
    """把 start_urls 里的 {page} 占位符按 page_start~page_end 展开。

    例：start_urls=["https://x.com/page/{page}/"], page 1~3
     →  [https://x.com/page/1/, https://x.com/page/2/, https://x.com/page/3/]

    💡 这是"列表页翻页"的流派一：URL 有规律就直接暴力枚举，
       简单可靠。流派二（跟着"下一页"链接走）在 extract_links_html 里。
    """
    urls: list[str] = []
    for page in range(rule.page_start, rule.page_end + 1):
        for u in rule.start_urls:
            if "{page}" in u:
                urls.append(u.replace("{page}", str(page)))
            elif page == rule.page_start:
                # 💡 地址里没有 {page} 的（单页），只在第一轮加进去，
                #    避免同一个地址重复 N 遍
                urls.append(u)
    # 去重保序
    # 🆕 一行去重的惯用法：seen.add(u) 的返回值是 None（falsy），
    #    所以 not (u in seen or seen.add(u)) =
    #      第一次见到 u → in 是 False → 继续算 seen.add(u) → None → not None=True → 保留
    #      重复见到 u → in 是 True → 短路，False → 丢弃
    #    可读性一般但极常用，看懂一次终身受用
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


# ----------------------------------------------------------------------------
# 抓取阶段 —— 阶段①：列表页 → 图集链接清单
# ----------------------------------------------------------------------------


def extract_links_html(f: Fetcher, rule: Rule) -> list[dict[str, str]]:
    """阶段一：从列表页收集图集链接。

    返回 [{"url": 图集地址, "title": 标题}, ...]，
    这个结构会一路传到主流程，用来生成下载目录名。
    """
    galleries: list[dict[str, str]] = []
    seen: set[str] = set()

    # 🆕 闭包（嵌套函数）：push 定义在 extract_links_html 内部，
    #    可以直接读写外层的 galleries 和 seen 变量。
    #    好处：不用把这两个参数传来传去，逻辑集中在"收录"这一个动作里
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
            # 💡 单页失败只记日志继续跑：抓 20 页挂 1 页，
            #    不值得为它放弃剩下 19 页（局部失败 ≠ 全局失败）
            log(f"  ✗ 列表页失败 {url} —— {e}")
            continue

        if rule.list_item:
            # 🆕 soup.select("css选择器") 返回所有匹配元素组成的列表
            nodes = soup.select(rule.list_item)
            log(f"  · {url} → 命中 {len(nodes)} 个条目")
            for n in nodes:
                # 💡 规则里 list_item 可能直接是 <a>，也可能包一层卡片 <div>。
                #    这行同时兼容两种写法："如果 n 自己是 a 就用它，
                #    否则在里面找第一个带 href 的 a"
                a = n if n.name == "a" else n.select_one("a[href]")
                if a is None:
                    continue
                href = abs_url(url, a.get("href", ""))
                if not href:
                    continue
                # 💡 list_url_regex：列表页通常混着"标签页/栏目页/搜索页"，
                #    用一条正则（如 /gallery/\d+/$）只留真正的图集
                if rule.list_url_regex and not re.search(rule.list_url_regex, href):
                    continue
                title = ""
                if rule.list_title:
                    t = n.select_one(rule.list_title)
                    title = t.get_text(" ", strip=True) if t else ""
                if not title:
                    # 💡 用户没指定标题选择器 → 用 best_title 多候选启发式
                    title = best_title(n, a, rule.list_title)
                push(href, title)

            # 翻页
            if rule.next_page and rule.max_pages > 1:
                cur, page = soup, 1
                while page < rule.max_pages:
                    nxt = cur.select_one(rule.next_page)
                    href = abs_url(url, nxt.get("href", "")) if nxt else ""
                    # 💡 两个停机条件：没有"下一页"了 / 翻回见过的页（防循环）
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
            # 💡 使用场景："我只知道某个详情页地址，帮我把这一页的图下了"
            push(url, "")
            break

    return galleries


def extract_links_json(f: Fetcher, rule: Rule) -> list[dict[str, str]]:
    """json 模式的阶段一：接口直接返回图集列表。

    💡 什么时候用 json 模式？F12 → Network → 刷新页面，
       如果看到一个 XHR 请求返回的 JSON 里就有图集列表
       （常见于前后端分离的站），直接调接口比解析 HTML 稳定得多。
    """
    galleries: list[dict[str, str]] = []
    seen: set[str] = set()
    for url in build_page_urls(rule):
        try:
            data = f.json(url)
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ 接口失败 {url} —— {e}")
            continue
        # 💡 dig()：用 "data.list[*]" 这样的路径字符串深挖嵌套 JSON
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


# ----------------------------------------------------------------------------
# 抓取阶段 —— 阶段②：详情页 → 图片地址清单（含图集内翻页）
# ----------------------------------------------------------------------------


def collect_image_urls(f: Fetcher, rule: Rule, page_url: str) -> list[str]:
    """阶段二：从详情页抽出图片地址（含图集内翻页）。

    💡 图集内翻页的两种流派（对应规则里二选一）：
       流派 A：gallery_page_url 模板  → URL 有规律，直接枚举
       流派 B：gallery_next 选择器    → 跟着"下一页"链接走
       都不配 = 详情页只有一页图，不翻。
    """
    urls: list[str] = []
    seen: set[str] = set()
    soup_cache: dict[str, BeautifulSoup] = {}

    def soup_of(u: str) -> BeautifulSoup:
        """同一页只解析一次（翻页时要用两次 soup 的时候省一次请求）。

        💡 简易 memoization（记忆化）：以 URL 为 key 缓存解析结果。
           典型场景：一页 HTML 既要取 <img> 又要找"下一页"链接，
           没缓存就得请求/解析两次。
        """
        k = url_key(u)
        if k not in soup_cache:
            soup_cache[k] = f.soup(u)[0]
        return soup_cache[k]

    def keep(u: str) -> bool:
        """图片地址的"安检门"：四道检查全过才算数。

        💡 这四道检查的顺序有讲究：便宜的在前（字符串操作），
           昂贵的在后（需要解析 URL 的）。虽然本例差距不大，
           但"先便宜后昂贵"是过滤器排序的通用原则。
        """
        if not u:
            return False
        low = u.lower()
        # ① JUNK 黑名单：头像/logo/广告位目录
        if JUNK_PAT.search(low):
            return False
        # ② 扩展名白名单：路径带扩展名但不是图片的（.html/.php）排除
        path = up.urlparse(low).path
        if os.path.splitext(path)[1] and os.path.splitext(path)[1] not in IMG_EXT:
            return False
        # ③④ 规则里的自定义白/黑名单（关键词包含判断）
        if rule.include_only and not any(k.lower() in low for k in rule.include_only):
            return False
        if rule.exclude and any(k.lower() in low for k in rule.exclude):
            return False
        return url_key(u) not in seen

    def harvest(cur_url: str) -> int:
        """抓一页，返回本页新增的图片数。

        💡 返回"新增数量"而不是图片列表：主流程要用它判断
           "这一页是不是空页"（gallery_stop_empty 的停机依据）。

        🆕 语法点：before = len(urls) 先记下出发点，
           结束时 return len(urls) - before 就是差值。
        """
        before = len(urls)
        if rule.type == "json":
            data = f.json(cur_url)
            vals = dig(data, rule.images_path) if rule.images_path else []
            for v in vals:
                if isinstance(v, dict):
                    # 💡 有的接口图片是 [{"url": "...", "w": 800}, ...] 对象数组
                    v = v.get(rule.image_field) or next(iter(v.values()), "")
                if isinstance(v, str):
                    u = apply_rewrites(abs_url(cur_url, v), rule.image_rewrite)
                    if keep(u):
                        seen.add(url_key(u))
                        urls.append(u)
        else:
            soup = soup_of(cur_url)
            for node in soup.select(rule.detail_images):
                # 三连处理：pick_url 抽地址（懒加载兼容）→ abs_url 补全 →
                # apply_rewrites 改写（缩略图→原图），然后过安检门
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
            # 💡 三根停机保险丝：
            # ① 空页即停：有些站翻超了会"回落"到第一页，不刹就死循环
            if added == 0 and rule.gallery_stop_empty and page > 1:
                break
            # ② 单图集总量上限
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
    """无规则模式：把页面上所有 <img>/<a 指向图片> 都收进来。

    💡 使用场景：临时想扒一个页面的图，懒得写规则。
       走的还是 pick_url（懒加载兼容）和 JUNK_PAT（垃圾过滤），
       只是选择器从"规则指定"退化成"全都要"。
    """
    soup, _ = f.soup(page_url)
    urls: list[str] = []
    seen: set[str] = set()
    for img in soup.find_all("img"):
        u = abs_url(page_url, pick_url(img))
        if u and not JUNK_PAT.search(u) and url_key(u) not in seen:
            seen.add(url_key(u))
            urls.append(u)
    # 💡 有些站的图片不放在 <img> 里，而是 <a href="xx.jpg"> 直接链到图片文件
    for a in soup.find_all("a", href=True):
        u = abs_url(page_url, a["href"])
        ext = os.path.splitext(up.urlparse(u).path)[1].lower()
        if ext in IMG_EXT and url_key(u) not in seen:
            seen.add(url_key(u))
            urls.append(u)
    return urls


# ----------------------------------------------------------------------------
# 下载阶段 —— 多线程把图片真正落到磁盘
# ----------------------------------------------------------------------------


@dataclass
class Stats:
    """下载统计计数器（线程安全版）。

    🆕 为什么要加锁？10 个线程同时执行 ok += 1，CPU 层面是
       三步：读值 → 加 1 → 写回。两线程交错执行就会"各读旧值、
       各写新值"，加完只多了 1 而不是 2（丢失更新）。
       Lock 保证这三步要么全做完、要么排队等，计数不丢。

    💡 锁只保护"一次自增"这么小的临界区，开销可忽略——
       不要因为怕慢就不加锁，计数错了更难查。
    """

    total: int = 0
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0
    # 🆕 field(default_factory=...) 在 dataclass 里同样适用于锁
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, **kw: int) -> None:
        """批量加计数：stats.add(ok=1, bytes=1024)。

        🆕 **kw 语法：把所有"关键字参数"收进一个字典。
           getattr/setattr 动态按名字读写字段，免得写一串 if。
        """
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
    """下载单个图片文件——全文件防御性编程最密集的函数。

    每一步都在防一种脏数据，读的时候问自己"这行在防什么"：

    🆕 语法点：dest 是 pathlib.Path 对象。
       dest.with_suffix(".part") = "同一路径换个扩展名"，
       用来生成临时文件路径，一行顶 os.path 的好几行。
    """
    # ① 断点续传：文件已存在且够大 → 当作以前下过，跳过
    #    （重跑任务、Ctrl+C 中断后续传，全靠这一条）
    if dest.exists() and dest.stat().st_size >= min_bytes:
        stats.add(skipped=1)
        log(f"    [{idx}/{total}] 已存在，跳过")
        return
    try:
        # referer 防盗链在 Fetcher.get 里注入；binary=True 走流式下载
        r = f.get(url, referer=referer, binary=True)
        # ② 先写 .part 临时文件，全部写完才"转正"
        # 💡 如果直接写目标文件，中途断网会留下半张损坏图，
        #    下次重跑还会把它当"已下载"跳过（因为存在）。
        #    .part 机制保证磁盘上永远只有完整文件。
        tmp = dest.with_suffix(dest.suffix + ".part")
        size = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            # 🆕 iter_content(64KB)：流式一块一块读，64KB 一块。
            #    大图几十 MB 也只占 64KB 内存
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fh.write(chunk)
                    size += len(chunk)
        ct = r.headers.get("Content-Type", "")
        r.close()

        # ③ 体积校验：小于 min_bytes 的多半是缩略图/占位 GIF/防盗链提示图
        if size < min_bytes:
            # 🆕 missing_ok=True：文件不存在也不报错（防竞态：别的线程刚删了）
            tmp.unlink(missing_ok=True)
            stats.add(skipped=1)
            log(f"    [{idx}/{total}] 体积过小({size}B)，疑似缩略图 → 丢弃")
            return
        # ④ Content-Type 校验：⚠️ 有些站被盗链时不回 404，
        #    而是回一张"请勿盗链"的 HTML 页（HTTP 200！）。
        #    只看状态码完全发现不了，必须看响应类型
        if ct and not ct.lower().startswith(("image/", "application/octet-stream")):
            tmp.unlink(missing_ok=True)
            stats.add(skipped=1)
            log(f"    [{idx}/{total}] 非图片({ct}) → 丢弃")
            return
        # ⑤ 修正扩展名：URL 写着 .jpg 实际可能是 webp
        real_ext = guess_ext(url, ct)
        final = dest.with_suffix(real_ext)
        if final != dest:
            final.unlink(missing_ok=True)
        # 🆕 Path.replace()：改名/移动，POSIX 上是原子操作——
        #    要么完全成功要么没发生，不会出现"改了一半"的中间态
        tmp.replace(final)
        stats.add(ok=1, bytes=size)
        log(f"    [{idx}/{total}] ✓ {final.name}  {size/1024:.0f} KB")
    except Exception as e:  # noqa: BLE001
        # 💡 下载失败绝不抛出：单张图失败不该炸掉整个线程池，
        #    记一笔 failed 继续下一张，最后统一汇总
        stats.add(failed=1)
        log(f"    [{idx}/{total}] ✗ {e}")


# ----------------------------------------------------------------------------
# 主流程 —— 把 ①列表收集 → ②地址解析 → ③并发下载 串成流水线
# ----------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """主入口。🆕 args 是 argparse 解析后的结果对象，
    命令行 --workers 8 会变成 args.workers == 8。

    返回值是进程退出码：0=成功，非 0=失败（给脚本/CI 判断用）。
    """
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
            # 🆕 {**g, "title": title} 字典解包合并：
            #    **g 把 g 的所有键值摊开，后面的 "title": title 覆盖同名键。
            #    即"复制 g，但把 title 换成净化过的版本"
            jobs.append(({**g, "title": title}, imgs))
            acc += len(imgs)
        # 够了就停手，不再白跑后面的详情页
        # 💡 "边解析边封顶"：想下 1000 张，解析到一个图集就累加计数，
        #    凑够立即 break。省的不只是流量，还有几百个详情页的
        #    解析时间和对目标站的请求量
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
        # 💡 目录名用 slugify 清洗过的标题；全空的给 gallery_001 兜底
        folder = out_root / slugify(g["title"], f"gallery_{gi:03d}")
        folder.mkdir(parents=True, exist_ok=True)
        log(f"\n📁 [{gi}/{len(jobs)}] {g['title']}  →  {folder}")
        referer = g["url"]
        n = len(imgs)
        # 🆕 ThreadPoolExecutor 线程池：
        #    with 块结束时自动等所有线程收工（不用手动 join）。
        #    pool.submit(fn, 参数...) 把任务扔进队列，池里空闲线程领着干。
        #    💡 爬虫是 IO 密集（99% 时间在等网络），多线程性价比极高
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = []
            for k, u in enumerate(imgs, 1):
                ext = guess_ext(u)
                # 💡 文件名用序号 001.jpg/002.jpg 而不是原名：
                #    保证排序稳定、文件名合法、不重名
                dest = folder / f"{k:03d}{ext}"
                futs.append(
                    pool.submit(
                        download_one, f, u, dest, referer, stats, args.min_bytes, k, n
                    )
                )
            # 🆕 as_completed：谁先完成谁先被 yield 出来；
            #    这里只用来"等全部完成"，所以循环体是 pass
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
# probe：帮你写规则 —— 输入一个页面，告诉你选择器该怎么填
# ----------------------------------------------------------------------------


def probe(args: argparse.Namespace) -> int:
    """探测页面结构：列出所有 img/a，并统计最常见的选择器。

    💡 写规则最痛苦的步骤是"选择器怎么填"。这个命令自动统计
       页面上出现次数最多的 class（img.xxx / div.xxx img / a.xxx），
       出现次数高的基本就是"图集卡片"和"正文图"，抄进规则即可。
    """
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
    """CLI 入口：定义所有命令行参数，然后分发到 run() 或 probe()。

    🆕 argparse 结构（三层）：
      ArgumentParser        → 总入口 python crawl.py
      add_subparsers        → 二级命令 run / probe
      add_argument          → 每个命令的选项

    💡 argv=None 时 argparse 自动取 sys.argv[1:]；
       传列表进来则用于测试（不用真的模拟命令行）。
    """
    p = argparse.ArgumentParser(
        prog="crawl.py",
        description="图集/写真类站点通用爬虫（规则驱动）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # 💡 epilog=__doc__：把文件顶部的模块文档字符串当作 --help 的
        #    附加说明，帮助信息只维护一份
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        """run 和 probe 共用的选项抽成一个函数，避免写两遍。

        💡 DRY 原则（Don't Repeat Yourself）：同样的东西只写一次。
        """
        sp.add_argument("--proxy", default=os.environ.get("PHOTOCRAWL_PROXY", ""),
                        help="代理：local=127.0.0.1:7890；none=真直连（忽略环境变量代理）；或直接写地址 http://ip:port")
        sp.add_argument("--timeout", type=int, default=20, help="单请求超时秒数（默认 20）")
        sp.add_argument("--retries", type=int, default=3, help="失败重试次数（默认 3）")
        sp.add_argument("--delay", type=float, default=0.0, help="每次请求后额外等待秒数（礼貌抓取）")
        sp.add_argument("--ignore-robots", action="store_true", help="忽略 robots.txt（自行确认合规性）")
        # 🆕 action="store_true"：布尔开关，出现即 True（--ignore-robots），
        #    不出现即 False，不需要跟值

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
        # 🆕 三元表达式："A if 条件 else B" 的单行 if/else，
        #    根据二级命令分发到对应函数
        return run(args) if args.cmd == "run" else probe(args)
    except KeyboardInterrupt:
        # 💡 Ctrl+C 的优雅退出：已下载的文件保留，重跑时
        #    download_one 的"已存在跳过"逻辑自动续传
        log("\n已中断（已下载的文件保留，重跑会自动跳过）")
        return 130


if __name__ == "__main__":
    sys.exit(main())
