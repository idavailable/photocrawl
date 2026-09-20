import sys, time
sys.path.insert(0,'.')
import urllib3; urllib3.disable_warnings()
from crawl import Fetcher

IMG = ("https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEj330jrJzH0WP7FqGpLBTT70rjjztNhyphenhyphenMJHLnDInAXtcSFOL5a6qzk_tPkl4BPS72KUBXfQ38uwhjzjy951Kcpd_X5OkODULqpP2tUZXLTl4vcjq3KJ4GNeJp4hZs-CGywaPiq75ycInwwoK3iICmYbeMkiLNk9CD8gN2EAOGFzlvFd0ARSX7eyqRI9JL4/h1000-e7/LE-LEDG-209B-GMS-33-4kUp-109.webp")
REF = "https://4kup.net/le-ledg-209b-gms/"

for label, kw in [("env代理(默认)", {}),
                  ("7890代理", {"proxy": "http://127.0.0.1:7890"}),
                  ("真直连", {"no_env": True})]:
    f = Fetcher(timeout=15, retries=1, ignore_robots=True, **kw)
    t0=time.time()
    try:
        r = f.get(IMG, referer=REF, binary=True)
        data = r.content
        print(f"  {label:14} → HTTP {r.status_code}  {len(data)/1024:.0f} KB  {time.time()-t0:.1f}s")
        r.close()
    except Exception as e:
        print(f"  {label:14} → ✗ {type(e).__name__}: {str(e)[:80]}  ({time.time()-t0:.1f}s)")

# 顺便看看 4kup 站点本身三条路都通不通
for label, kw in [("env代理(默认)", {}), ("7890代理", {"proxy": "http://127.0.0.1:7890"}), ("真直连", {"no_env": True})]:
    f = Fetcher(timeout=15, retries=1, ignore_robots=True, **kw)
    try:
        r = f.get("https://4kup.net/le-ledg-209b-gms/")
        print(f"  4kup 站点 {label:14} → {r.status_code} {len(r.content)}B")
    except Exception as e:
        print(f"  4kup 站点 {label:14} → ✗ {type(e).__name__}")
