# photocrawl — 图集/写真类站点通用爬虫

规则驱动的图片批量抓取工具。引擎和站点规则分开：**换站点只改一份 JSON，不动代码**。

> 📖 想看懂实现原理？读 [docs/TUTORIAL.md](docs/TUTORIAL.md) —— 逐模块拆解源码、
> 讲透每个设计决策（懒加载、防盗链、trust_env 坑、并发下载……），新手向。

## 安装

依赖已装在隔离环境里（不污染系统 Python）：

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
"$PY" crawl.py --help
```

想自己建环境：

```bash
python -m venv .venv
.venv/Scripts/pip install --proxy http://127.0.0.1:7890 -i https://pypi.org/simple \
    requests beautifulsoup4 lxml
```

> 本机实测：直连 `pypi.org` 不通、清华镜像在这个代理环境下也拉不到包，
> **必须用 `--proxy http://127.0.0.1:7890 -i https://pypi.org/simple`** 才能装上。

## 三步上手

### ① 探测页面结构（写规则的第一步）

```bash
"$PY" crawl.py probe "https://某站.com/gallery/123" --proxy local
```

会打印页面上所有 `<img>`（含懒加载属性、实际 URL、class）和所有 `<a>`，
并**按出现次数统计出候选选择器**，直接抄进规则就行。

### ② 写规则

复制 `rules/example.json`（HTML 站）或 `rules/example-json-api.json`（接口站）改一改。

### ③ 先试跑，再下载

```bash
# 试跑：只列出将要下载什么，不落盘 —— 必做，能提前发现选择器写错
"$PY" crawl.py run --rule rules/某站.json --proxy local --dry-run

# 只收集图集清单（不解析详情页），调规则时用这个最快
"$PY" crawl.py run --rule rules/某站.json --proxy local --list-only

# 正式下载
"$PY" crawl.py run --rule rules/某站.json --proxy local --out ./下载 --workers 8

# 封顶：本次最多下 1000 张（边解析边封顶，不会白跑后面的详情页）
"$PY" crawl.py run --rule rules/某站.json --out ./下载 --max-total 1000
```

## 代理怎么选

`--proxy` 有三档：

| 取值 | 行为 |
|---|---|
| `local` | 走 `http://127.0.0.1:7890`（本机 mihomo/clash） |
| `none` | 真·直连，**同时忽略环境变量里的 `HTTP(S)_PROXY`** |
| 不填 | 交给环境变量（有 `HTTPS_PROXY` 就用，没有就直连） |
| `http://ip:port` | 用指定代理 |

> ⚠️ 踩过的坑：Python 的 `requests` 里，**环境变量代理的优先级高于 `session.proxies`**。
> 有些沙箱/IDE 会预设 `HTTPS_PROXY=http://127.0.0.1:xxxxx`，这时你 `--proxy local` 会“看起来没生效”
> （报错里是目标站的 SSL 错，而不是代理错）。本工具在显式指定代理时会自动 `trust_env = False` 规避。

## 其它用法

```bash
# 没有规则，快速扒单个页面/图集
"$PY" crawl.py run --auto "https://某站.com/gallery/123" --out ./下载 --proxy local

# 已有一份链接清单（一行一个，# 为注释）
"$PY" crawl.py run --from-file urls.txt --out ./下载 --proxy local

# 用规则，但只抓指定的几个详情页（跳过列表页）
"$PY" crawl.py run --rule rules/某站.json --urls "https://…/1" "https://…/2" --proxy local
```

## 规则字段速查

| 字段 | 作用 |
|---|---|
| `start_urls` | 列表页入口，`{page}` 会按 `page_start`~`page_end` 展开 |
| `list_item` | 列表页里每个「图集」的选择器；留空则把 `start_urls` 本身当详情页 |
| `list_title` | 相对条目的标题选择器（决定文件夹名），留空则取条目文字 |
| `list_url_regex` | 只保留 href 匹配该正则的条目（**目录页/标签页混在列表里时必填**） |
| `next_page` + `max_pages` | 用「下一页」按钮翻页时填，最多翻几页 |
| `detail_images` | 详情页图片选择器；也可填 `a` 配合 `image_attrs:["href"]` 取原图 |
| `image_attrs` | 按顺序尝试的属性，**懒加载站点必填**，如 `["data-original","data-src","src"]` |
| `gallery_next` | 一个图集分多页时，填「下一页」选择器，会自动翻完 |
| `gallery_page_url` | 图集内翻页用 URL 模板（含 `{base}` 与 `{page}`），适合 `?page=N` 或 `/N/` 型 |
| `gallery_pages` | 配合上行，最多翻几页 |
| `gallery_stop_empty` | 某页没抓到新图就停（默认 true），**防无效页回落到第一页导致死循环** |
| `image_rewrite` | 地址改写，如 `[["/thumbnail/","/"]]` 缩略图换原图 |
| `min_bytes` | 小于该体积丢弃（默认 8KB），挡缩略图 |
| `exclude` / `include_only` | 按地址关键词白/黑名单 |
| `max_images` | 单个图集上限，0 = 不限 |
| `referer` | 下载图片时的 Referer，留空自动用详情页地址（**防盗链站点靠它**） |

接口站额外用：`type: "json"`、`items_path`、`item_url_field`、`item_title_field`、
`images_path`、`image_field`、`gallery_next`。路径支持 `data.list[*].banner` 这种写法。

## 已内建的健壮性

- **断点续传**：已存在且体积达标的文件直接跳过，中断后重跑即可
- **自动纠正扩展名**：按 `Content-Type` 判断真实格式（URL 写 `.jpg` 实际是 webp 也能救回来）
- **重试 + 退避**：默认 3 次，间隔递增
- **防盗链**：自动带 Referer；失败可显式指定
- **乱序并发**：`--workers` 控制线程数，每线程独立 Session
- **过滤垃圾图**：头像/图标/占位图/广告位按路径关键词剔除；非 `image/*` 响应丢弃
- **反爬礼貌**：默认遵守 `robots.txt`，`--delay` 可加请求间隔
- **来源可追溯**：每个图集写 `_meta.json`，根目录写 `index.json` 汇总

## 输出结构

```
下载/
├── index.json                  # 全部图集汇总（来源、张数、路径）
├── 图集A/
│   ├── 001.jpg
│   ├── 002.jpg
│   └── _meta.json              # 标题、来源 URL、全部图片地址、抓取时间
└── 图集B/
    └── …
```

## 已验证

用 `books.toscrape.com`（官方爬虫练习站，允许抓取）跑通全链路：
列表页翻页 → 40 个条目 → 详情页抽图 → 并发下载 → 写 meta，见 `rules/demo-books.json`。

```bash
"$PY" crawl.py run --rule rules/demo-books.json --proxy local --out ./downloads
```

## 站点支持一览（实测）

| 站点 | 规则文件 | 代理 | 单图集张数 | 取图方式 | 备注 |
|---|---|---|---|---|---|
| ilovexs.com | `rules/ilovexs.json` | 不需要 | 35–80 | `img.gallery-image` 直出 webp | 列表分 `/page/N/` 与分类页，量大 |
| 4kup.net | `rules/4kup.json` | 不需要 | 20 | `a.thumb-photo[href]` → blogger 原图 | 列表 `/page/N/`，共 2200+ 页 |
| taotu.org | `rules/taotu.json` | 需要 | 29–95 | `a[href]` 指向原图，缩略图在 `src` | 只有首页可达，`/page/N/` 全 404 |
| misskon.com | `rules/misskon.json` | 需要 | 36（3 页×12） | `img.aligncenter.lazy` 的 `data-src` | 用 `gallery_page_url: "{base}{page}/"` 翻页 |
| v2ph.com | `rules/v2ph.json` | 需要 | 10 | `img.img-fluid.album-photo` 直出 jpg | 图片防盗链，必须带 Referer |
| yituyu.com | `rules/yituyu.json` | 不需要 | 5 | `div.gallerypic img` 的 `data-src` | 免费只放 5 张预览，整本要 VIP |

**踩过的坑**

- `JUNK_PAT` 早期版本里的 `ads?/` 会把 `wp-content/**uploads**/` 里的 `ads/` 当成广告目录命中，
  导致 ilovexs 整站图片全被过滤。现已改成带路径边界的 `(?:^|/)ads?/`。
  —— 写这类黑名单正则时，**任何要加 `/` 的词都必须带左边界**。
- 环境变量 `HTTPS_PROXY` 会顶掉 `session.proxies`（见上文「代理怎么选」）。
- `?page=2` 型站点有时无效页会**回落到第一页内容**（如 misskon 的 `/5/`），
  靠 `gallery_stop_empty` 检测「本页新增 0 张」自动收手。
- taotu 这类站缩略图和原图**同名不同路径**，且原图路径没有统一规律，
  只能从缩略图外层 `<a href>` 取——所以规则里用 `detail_images: "a"` + `image_attrs: ["href"]`。

## 使用须知

- 只抓**你有权访问、且允许抓取**的公开内容；`robots.txt` 默认遵守（`--ignore-robots` 需自行确认合规）
- 抓取频率放低一些（`--delay 0.5`），别把人家站点打挂
- 下载的内容版权归原作者，自己看可以，**别再传播/商用**
- 请勿用于抓取需要登录才能看的私密内容
