# photocrawl 实现拆解 —— 写给第一次看爬虫源码的你

> 配套代码：[`crawl.py`](../crawl.py)（约 1000 行）。
> 这篇教程不讲"怎么用"，只讲"它是怎么实现的"。建议边看代码边对照。

---

## 0. 先建立一个世界观：爬虫就是三步

浏览器看网页很复杂，但对爬虫来说，任何图片站都只是三步：

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ ① 请求       │ →  │ ② 解析       │ →  │ ③ 下载       │
│ 拿到 HTML    │    │ 抽出图片地址  │    │ 存成文件      │
└─────────────┘    └─────────────┘    └─────────────┘
   Fetcher          collect_image_urls   download_one
```

每一"格"在 `crawl.py` 里都有对应的实现。整个文件的结构地图：

| 代码区 | 行号大约 | 干什么 |
|---|---|---|
| 常量与工具函数 | 57~270 | UA、懒加载属性表、正则黑名单、URL 拼接 |
| `Fetcher` 网络层 | 277~399 | 发请求：代理、重试、robots.txt |
| `Rule` 规则 | 407~456 | 把"某个站怎么爬"写成一份配置 |
| `extract_links_*` | 476~573 | 阶段①：从列表页收集图集链接 |
| `collect_image_urls` | 576~673 | 阶段②：从详情页抽图片地址 |
| `download_one` + `Stats` | 700~762 | 阶段③：并发下载单个文件 |
| `run` 主流程 | 770~940 | 把三步串起来 |
| `probe` / CLI | 948~1051 | 命令行入口和调试工具 |

---

## 1. 网络层 `Fetcher`：不是一个裸的 requests.get

新手版爬虫长这样：

```python
html = requests.get(url).text          # 挂了就挂了
img = requests.get(img_url).content    # 被网站拉黑就拉黑
```

工程版要解决四个问题：**被封、失败、合规、并发**。

### 1.1 Session 复用 + 伪装浏览器（第 294~316 行）

```python
@property
def session(self) -> requests.Session:
    s = getattr(self._local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,...", ...})
        ...
        self._local.s = s
    return s
```

两个知识点：

- **Session 能复用 TCP 连接**。下载 600 张图，裸 `requests.get` 要握手 600 次，Session 走 keep-alive 一次握手反复用，快好几倍。
- **`threading.local()` 是"每个线程一份 Session"**。requests 的 Session 不是线程安全的，10 个下载线程如果共用一个 Session 会出诡异 bug。`_local.s` 保证线程 A 和线程 B 各拿各的，互不干扰。这是多线程爬虫的标准写法，值得背下来。

User-Agent 为什么要伪装？因为 `python-requests/2.x` 这个默认 UA 一眼就会被识别成爬虫，直接 403。

### 1.2 代理的坑：环境变量会顶掉你显式设置的代理（第 308~314 行）

```python
if self.proxy:
    s.proxies = {"http": self.proxy, "https": self.proxy}
    # 关键：环境变量里的 HTTP(S)_PROXY 优先级高于 session.proxies，
    # 不关掉 trust_env 的话显式指定的代理会被环境代理顶掉。
    s.trust_env = False
```

这是本项目**实战踩过的最阴的坑**。现象是：明明写了 `--proxy http://127.0.0.1:7890`，报错却是目标站的 SSL 错误，看起来代理"没生效"。

原因：如果系统环境变量里存在 `HTTPS_PROXY=xxx`，requests 会**优先**用它，把你 `session.proxies` 里写的直接无视。解法就是 `trust_env = False`——告诉 requests"别自作主张读环境变量"。

另外 `--proxy none` 走 `no_env=True` 分支：什么都不设置但也关掉 trust_env，实现"真·直连"。

### 1.3 重试 + 指数退避（第 344~376 行）

```python
for attempt in range(1, self.retries + 1):
    try:
        r = self.session.get(url, ...)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} {url}")
        return r
    except Exception as e:
        last = e
        if attempt < self.retries:
            time.sleep(min(2**attempt * 0.6, 8) + random.uniform(0, 0.4))
```

- 失败不立刻重试，而是等 `0.6 * 2^attempt` 秒：第 1 次等 1.2s、第 2 次等 2.4s……这叫**指数退避**。网站刚 502，你 0 秒后重试只会再吃一个 502。
- `+ random.uniform(0, 0.4)` 加一点随机抖动，避免多个线程"整点同时重试"形成请求风暴。
- 404 单独拎出来**不重试**（`except FileNotFoundError: raise`）——页面不存在，重试一万次也是 404，浪费时间。

### 1.4 robots.txt：爬虫的自我修养（第 319~341 行）

```python
rp = robotparser.RobotFileParser()
rp.parse(r.text.splitlines())
...
return rp.can_fetch(UA, url)
```

`robots.txt` 是网站放在根目录的"抓取须知"，声明哪些路径欢迎爬、哪些不欢迎。`urllib.robotparser` 是标准库自带的解析器。按规矩：robots 禁止的路径就不去碰。这份代码默认开启检查，想绕过必须显式加 `--ignore-robots`，把决定权留给人。

---

## 2. 解析层：从 HTML 里抠出图片地址

### 2.1 懒加载：为什么 `img['src']` 经常是空的（第 145~159 行）

现代图片站为了省流量，页面刚打开时 `<img>` 长这样：

```html
<img data-original="https://.../real.jpg" src="data:image/gif;base64,R0lGOD...">
```

`src` 是一个 1 像素的占位 GIF，真图藏在 `data-original` 里，等用户滚动到可视区域才由 JS 换进去。**爬虫没有浏览器，JS 永远不执行**，所以直接取 `src` 会拿到一堆 1KB 的灰色占位图。

解法是按优先级猜属性：

```python
LAZY_ATTRS = ["data-original", "data-original-src", "data-src",
              "data-lazy-src", "data-echo", ..., "src"]

def pick_url(tag, attrs=None):
    for a in (attrs or LAZY_ATTRS):
        v = tag.get(a)
        if v and isinstance(v, str) and v.strip():
            return v.strip()      # 第一个有值的属性就是真图
```

先查所有 `data-*` 懒加载属性，全没有才退回 `src`。规则 JSON 里的 `image_attrs` 字段可以覆盖这套默认顺序。

### 2.2 垃圾图黑名单（第 83~89 行）

```python
JUNK_PAT = re.compile(
    r"(avatar|logo|icon|sprite|placeholder|loading|blank|spacer|pixel|"
    r"advert|(?:^|/)ads?/|banner|button|emoji|favicon|thumb[-_]?icon)",
    re.I,
)
```

页面里除了正文图还有头像、logo、广告位。这个正则按 URL 关键词过滤它们。

注意 `ads?/` 前面那串 `(?:^|/)`——这是实战改出来的。最初版本只写了 `ads?/`，结果把 WordPress 图床路径 `wp-content/**uploads**/2026/09/ads**xxx**.webp` 里恰好含 "ads" 的文件名误杀了，一整站 50 张图被过滤到 0 张，而且**没有任何报错**。教训：写"关键词黑名单"正则时，一定想清楚它会在 URL 的哪个位置匹配，该加边界就加边界。

### 2.3 相对地址补全（第 123~135 行）

HTML 里图片地址有四种形态：

```html
https://cdn.site.com/a.jpg    绝对地址
//cdn.site.com/a.jpg          协议相对（跟随当前页的 http/https）
/img/2026/a.jpg               相对路径（挂在当前域名下）
javascript:void(0)            垃圾，直接丢弃
```

`abs_url()` 用标准库 `urljoin` 统一补成绝对地址，并对 `//` 开头和 `javascript:` 等特殊情况分别处理。**永远不要自己手写字符串拼接去拼 URL**，边界情况比你想象的多。

### 2.4 标题去哪了：`best_title()`（第 193~220 行）

列表页的链接文字经常被服务器截断：`森萝财团: 穿JK的学妹 XX...`。完整标题往往藏在 `title` 属性或 `img[alt]` 里。

```python
cands = [选择器取的标题, a['title'], 节点全文, img['alt'], ...]
complete = [c for c in cands if not c.endswith(("...", "…"))]
pool = complete or cands
return max(pool, key=len)[:maxlen]     # 优先选"不以省略号结尾且最长"的
```

思路不是"修字符串"，而是**收集所有候选，用启发式规则挑最好的一个**。这比判断"哪里截断了"可靠得多。

---

## 3. 规则系统：换站不改代码的关键

### 3.1 为什么不用代码写死每个站

10 个站 10 种结构，如果都写成 `if site == "taotu": ...`，代码会迅速腐化。这里用**配置与引擎分离**：

```python
@dataclass
class Rule:
    start_urls: list[str]        # 列表页地址，{page} 是翻页占位符
    list_item: str               # 列表页里"图集卡片"的 CSS 选择器
    detail_images: str           # 详情页里"图片"的 CSS 选择器
    image_attrs: list[str]       # 懒加载属性顺序
    min_bytes: int = 8192        # 小于 8KB 视为缩略图
    ...
```

`@dataclass` 自动生成 `__init__`/字段校验；`Rule.load()`（第 449 行）从 JSON 读入并用 `Rule.__dataclass_fields__` 检查未知字段——你 JSON 里手滑写错字段名，它不会静默忽略，而是警告你。

一份规则（`rules/yituyu.json`）长这样：

```json
{
  "name": "yituyu",
  "start_urls": ["https://www.yituyu.com/gallery/page/{page}/"],
  "page_start": 1, "page_end": 3,
  "list_item": "div.item a",
  "list_url_regex": "/gallery/\\d+/?$",
  "detail_images": "div.gallery-content img",
  "image_attrs": ["data-original", "data-src", "src"]
}
```

### 3.2 翻页：两种流派（第 630~669 行）

**流派一：URL 模板**。列表页地址有规律（`/page/2/`、`?page=2`），直接暴力展开：

```python
def build_page_urls(rule):        # 第 458 行
    for page in range(rule.page_start, rule.page_end + 1):
        urls.append(u.replace("{page}", str(page)))
```

简单粗暴，绝大多数站够用。详情页内的多页图集同理（`gallery_page_url` + `gallery_pages`）。

**流派二：跟着"下一页"按钮走**。地址没规律时，找翻页元素一直点：

```python
nxt = soup.select_one(rule.gallery_next)     # 找"下一页"的 <a>
cur_url = abs_url(cur_url, nxt["href"])      # 跳过去
```

配套三个保险丝，防止爬虫在坏站上转圈：

- `visited_pages` 集合：访问过的页不再进（防循环）
- 图集内最多翻 50 页（防死循环）
- `gallery_stop_empty`：某页一张新图都没抓到就停（有些站翻超了会"回落"到第一页）

---

## 4. 下载层：并发、断点续传、防"假图"

### 4.1 线程池（第 895~906 行）

```python
with ThreadPoolExecutor(max_workers=args.workers) as pool:
    futs = [pool.submit(download_one, f, u, dest, ...) for k, u in enumerate(imgs, 1)]
    for _ in as_completed(futs):
        pass
```

下载 600 张图，串行要 10 分钟，开 10 个线程 1 分钟。爬虫是典型的 **IO 密集**任务——99% 的时间在等网络响应，CPU 几乎闲着，所以多线程性价比极高（不需要多进程/协程）。`as_completed` 逐个收割完成的 future。

### 4.2 下载一个文件的完整防御（`download_one`，第 715~762 行）

这是全文件"防御性编程"最密集的函数，每一行都在防一种脏数据：

```python
# ① 断点续传：文件已存在且够大 → 跳过
if dest.exists() and dest.stat().st_size >= min_bytes:
    stats.add(skipped=1); return

# ② 先写 .part 临时文件，全部写完才改名为正式文件
tmp = dest.with_suffix(dest.suffix + ".part")
...
tmp.replace(final)

# ③ 体积校验：小于 8KB 大概率是缩略图/占位图/防盗链提示图
if size < min_bytes:
    tmp.unlink(missing_ok=True); return

# ④ Content-Type 校验：有些站被盗链时不回 404，而是回一张"请勿盗链"的 HTML 页
if ct and not ct.lower().startswith(("image/", ...)):
    tmp.unlink(missing_ok=True); return

# ⑤ 修正扩展名：URL 写着 .jpg 实际可能是 webp
real_ext = guess_ext(url, ct)
final = dest.with_suffix(real_ext)
```

**`.part` 临时文件**是核心细节：直接写目标文件的话，中途崩溃会留下半张损坏的图，下次重跑还会把它当"已下载"跳过。先写 `.part`、成功后 `replace()`（原子操作），保证磁盘上永远只有完整文件。

**防盗链**在哪？在 `f.get(url, referer=referer, binary=True)`——很多图床检查 HTTP 的 `Referer` 头，不是从自家页面跳来的请求就拒绝。带上详情页地址当 Referer 即可，这就是规则里 `referer` 字段的用途。

### 4.3 并发统计要加锁（第 700~712 行）

```python
@dataclass
class Stats:
    ok: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)
```

10 个线程同时 `stats.ok += 1`，会丢计数（读-改-写三步可能交错）。一个 Lock 就够——统计这种轻量操作，锁的开销可以忽略。

---

## 5. 主流程的编排技巧

`run()`（第 770 行起）本身逻辑简单，但有两处值得学：

**`--max-total` 的"边解析边封顶"**（第 845~848 行）：想下 1000 张，如果先解析完全部 500 个图集再截断，等于多解析了几百个详情页。这里是解析完一个图集就累加计数，凑够额度立即 `break`——省下的不只是流量，还有解析详情页的时间和对目标站点的请求量。

**两级 metadata**：每个图集目录写 `_meta.json`（来源 URL、原始地址列表、抓取时间），根目录写 `index.json`（总清单）。以后想删重、想溯源、想换工具重新组织，都有据可查。

---

## 6. 实战演练：给一个新站写规则（10 分钟）

1. **probe 探路**：
   ```bash
   python crawl.py probe "https://某站.com/gallery/123" --proxy local
   ```
   它会打印页面上所有 `<img>`（含属性、class）和 `<a>`，并**自动统计出现次数最多的 class，给出选择器建议**——这个建议直接抄进规则就行。

2. **写规则**：建 `rules/mysite.json`，填 `start_urls`、`list_item`、`detail_images`。拿不准就在浏览器按 F12，右键元素 → Copy selector。

3. **dry-run 验证**（必做！）：
   ```bash
   python crawl.py run --rule rules/mysite.json --dry-run --preview 5
   ```
   看输出的地址是不是原图（有没有 `data:image` 占位符、有没有混进头像 logo）。

4. **正式下载**：
   ```bash
   python crawl.py run --rule rules/mysite.json --out ./downloads --workers 8 --proxy local
   ```

调试顺序铁律：**probe → list-only → dry-run → run**，逐级放大，别一上来就全量跑。

---

## 7. 实战踩坑实录（比代码更值钱）

| # | 坑 | 现象 | 解法 |
|---|---|---|---|
| 1 | 环境变量代理优先级高于 `session.proxies` | `--proxy` 写了却像没生效，报目标站 SSL 错 | `session.trust_env = False` |
| 2 | `JUNK_PAT` 误杀 | 整站 50 张图被过滤成 0 张，无报错 | `ads?/` 加路径边界 `(?:^|/)` |
| 3 | 图床和主站网络路线不同 | 主站直连可达，blogger 图床却必须走代理 | 分别测三条路（直连/代理），规则里单独处理 |
| 4 | 列表页标题被截断 | 文件夹名全是 `XXX ...` | `best_title()` 收集多候选选最完整 |
| 5 | 429/502 瞬时抖动 | 大批量下载时成片失败 | 指数退避重试 + 降低 workers |

共同规律：**爬虫的 bug 大多不报错，而是"静默少爬"或"静默爬错"**。所以防御要放在数据校验上（体积、Content-Type、魔数），而不是只靠 try/except。

---

## 8. 合规清单

- 遵守 robots.txt（默认开启，`--ignore-robots` 需人为确认）
- 控制并发和频率（`--delay`、`--workers`），别把小站打挂
- **不绕付费墙/登录墙**——要登录才能看的内容，停手
- 下载的图片仅限个人浏览，**不传播、不商用**（版权属于原作者）

---

## 9. 你可以动手改的点（练习题）

1. 给 `download_one` 加文件**魔数校验**：读前 16 字节，不符合 jpg/png/webp 签名的删掉（比 Content-Type 更硬的验证）。
2. 把 `Fetcher.stats` 改成按**域名**统计请求次数，超阈值自动 sleep——模拟一个简单的限速器。
3. 支持 `rules/*.json` 里的 `download_headers` 字段，让每个站自定义下载请求头。
4. 给 `probe` 加 `--json` 输出，把选择器建议导出成规则草稿。

改完跑 `python crawl.py run --rule rules/demo-books.json --dry-run`（demo 规则指向练习站 books.toscrape.com，专门给人练手的），不伤真实站点。
