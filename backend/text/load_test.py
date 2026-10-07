"""
接口压测脚本（只用 httpx + 标准库，不引入额外依赖）

用法示例
--------
# 1) 只压不鉴权的读接口（最安全，先跑这个）
python text/load_test.py --scenario news --users 50 --duration 30

# 2) 压聊天接口（需要账号，因为聊天有 20 次/分钟/用户的限流）
python text/load_test.py --scenario chat --users 10 --duration 60 --accounts 10

# 3) 混合场景（默认权重：新闻 60% / 聊天 30% / 登录 10%）
python text/load_test.py --scenario mixed --users 40 --duration 60 --accounts 10

# 4) 只压登录
python text/load_test.py --scenario login --users 20 --duration 20

输出
----
- 屏幕上打印一份带 QPS、延迟分位数的报告
- 同时把明细存成 text/load_test_<场景>_<时间>.json，方便把数字写进简历

注意（很重要）
--------------
聊天接口有「每用户每分钟 20 次」的限流。所以压聊天时：
  - 用 --accounts N 开 N 个测试账号，把并发分散到不同用户身上，配额会变成 20×N 次/分钟
  - 被限流返回的 429 **不算失败**，会单独统计成「限流命中」
  - 如果你的目标是测聊天接口的吞吐上限，先把 cache/chat_cache.py 里的
    RATELIMIT_MAX 调大（或临时改成 999999），压完记得改回来
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import string
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import httpx

# Windows 控制台默认是 GBK，中文会乱码，这里强制切到 UTF-8
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 压测时用的提问，故意覆盖三类意图，避免只压到同一条分支
CHAT_QUESTIONS = [
    "今天有什么科技新闻？",
    "最近有什么体育方面的新闻？",
    "帮我生成我的月度阅读报告",
    "你好，你能做什么？",
    "有没有关于经济的新闻？",
]


# ----------------------------------------------------------------------
# 数据收集
# ----------------------------------------------------------------------
class Stats:
    """收集所有请求的结果，最后统一算分位数"""

    def __init__(self) -> None:
        self.total = 0
        self.ok = 0
        self.ratelimited = 0
        self.failed = 0
        self.latencies: list[float] = []          # 毫秒，成功请求的端到端耗时
        self.ttfb: list[float] = []               # 毫秒，流式接口的首字延迟
        self.by_endpoint: dict[str, dict] = defaultdict(
            lambda: {"n": 0, "ok": 0, "rl": 0, "fail": 0, "lat": [], "ttfb": []}
        )
        self.errors: Counter = Counter()

    def record(
        self,
        endpoint: str,
        latency_ms: float,
        ok: bool,
        status: int,
        ttfb_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        self.total += 1
        slot = self.by_endpoint[endpoint]
        slot["n"] += 1

        if status == 429:
            self.ratelimited += 1
            slot["rl"] += 1
            return

        if ok:
            self.ok += 1
            slot["ok"] += 1
            self.latencies.append(latency_ms)
            slot["lat"].append(latency_ms)
            if ttfb_ms is not None:
                self.ttfb.append(ttfb_ms)
                slot["ttfb"].append(ttfb_ms)
        else:
            self.failed += 1
            slot["fail"] += 1
            self.errors[f"{endpoint} | HTTP {status} | {error or ''}".strip()] += 1


def percentile(values: list[float], p: float) -> float:
    """线性插值算分位数，避免 statistics.quantiles 在小样本下报错"""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# ----------------------------------------------------------------------
# 账号：注册或登录，拿到 token
# ----------------------------------------------------------------------
def rand_name(prefix: str = "lt") -> str:
    tail = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}_{tail}"


async def prepare_accounts(client: httpx.AsyncClient, base_url: str, n: int) -> list[str]:
    """
    准备 n 个可用账号，返回 token 列表。
    先尝试注册；如果用户名已存在（400），就退回登录。
    """
    tokens: list[str] = []
    for i in range(n):
        username = f"loadtest_{i:03d}"
        password = "loadtest123"
        payload = {"username": username, "password": password}

        r = await client.post(f"{base_url}/api/user/register", json=payload)
        if r.status_code != 200:
            r = await client.post(f"{base_url}/api/user/login", json=payload)

        try:
            body = r.json()
            token = body["data"]["token"]
            tokens.append(token)
        except Exception:
            print(f"  [警告] 账号 {username} 准备失败：HTTP {r.status_code} {r.text[:120]}")

    if not tokens:
        raise SystemExit(
            "无法准备任何测试账号。请确认：\n"
            "  1) 服务已启动  2) MySQL 可连接  3) --base-url 正确"
        )
    return tokens


async def prepare_sessions(
    client: httpx.AsyncClient, base_url: str, tokens: list[str], per_account: int
) -> dict[str, list[int]]:
    """给每个账号预建若干会话，避免压测过程中反复建会话干扰结果"""
    out: dict[str, list[int]] = {}
    for token in tokens:
        ids: list[int] = []
        for _ in range(per_account):
            r = await client.post(
                f"{base_url}/api/chat/session",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                ids.append(r.json()["data"]["sessionId"])
            except Exception:
                pass
        out[token] = ids or [0]   # 0 表示没建成功，交给接口自己报错
    return out


# ----------------------------------------------------------------------
# 单个请求
# ----------------------------------------------------------------------
async def hit_news_categories(client: httpx.AsyncClient, base_url: str) -> tuple[int, str | None]:
    r = await client.get(f"{base_url}/api/news/categories")
    return r.status_code, None if r.status_code == 200 else r.text[:200]


async def hit_news_list(
    client: httpx.AsyncClient, base_url: str, category_id: int
) -> tuple[int, str | None]:
    r = await client.get(
        f"{base_url}/api/news/list",
        params={"categoryId": category_id, "page": random.randint(1, 3), "pageSize": 10},
    )
    return r.status_code, None if r.status_code == 200 else r.text[:200]


async def hit_news_detail(
    client: httpx.AsyncClient, base_url: str, news_id: int
) -> tuple[int, str | None]:
    r = await client.get(f"{base_url}/api/news/detail", params={"id": news_id})
    return r.status_code, None if r.status_code == 200 else r.text[:200]


async def hit_login(client: httpx.AsyncClient, base_url: str) -> tuple[int, str | None]:
    r = await client.post(
        f"{base_url}/api/user/login",
        json={"username": "loadtest_000", "password": "loadtest123"},
    )
    return r.status_code, None if r.status_code == 200 else r.text[:200]


async def hit_chat_stream(
    client: httpx.AsyncClient, base_url: str, token: str, session_id: int
) -> tuple[int, str | None, float | None]:
    """
    调用流式聊天接口。
    返回 (状态码, 错误信息, 首字延迟毫秒)。
    每收到一个 content 事件就算一次「首字」——只记第一次。
    """
    payload = {"sessionId": session_id, "message": random.choice(CHAT_QUESTIONS)}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    t0 = time.perf_counter()
    ttfb: float | None = None
    buf = ""

    async with client.stream("POST", f"{base_url}/api/chat/stream", json=payload, headers=headers) as r:
        if r.status_code != 200:
            await r.aread()
            return r.status_code, r.text[:200], None

        try:
            async for chunk in r.aiter_text():
                buf += chunk
                # SSE 一条事件以空行结尾
                while "\n\n" in buf:
                    raw, buf = buf.split("\n\n", 1)
                    for line in raw.splitlines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            evt = json.loads(line[5:].strip())
                        except Exception:
                            continue
                        if evt.get("type") == "content" and ttfb is None:
                            ttfb = (time.perf_counter() - t0) * 1000
                        elif evt.get("type") == "error":
                            return 200, evt.get("message", "SSE error"), ttfb
        except httpx.RemoteProtocolError:
            # 服务端提前断开，仍算一次成功（响应已经拿到了）
            pass

    return 200, None, ttfb


# ----------------------------------------------------------------------
# 工作协程
# ----------------------------------------------------------------------
async def worker(
    name: str,
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    stats: Stats,
    stop_at: float,
    tokens: list[str],
    sessions: dict[str, list[int]],
    category_ids: list[int],
    news_ids: list[int],
) -> None:
    mix = {
        "news":  ("news_categories", "news_list", "news_detail"),
        "chat":  ("chat_stream",),
        "login": ("login",),
    }
    plan = mix.get(args.scenario)
    if plan is None:                      # mixed
        plan = ("news_categories", "news_list", "news_detail",
                "chat_stream", "chat_stream", "chat_stream", "login")

    while time.perf_counter() < stop_at:
        endpoint = random.choice(plan)

        # 依赖数据缺失时跳过，避免把「没数据」算成失败
        if endpoint == "news_list" and not category_ids:
            continue
        if endpoint == "news_detail" and not news_ids:
            continue
        if endpoint == "chat_stream" and (not tokens or not sessions):
            continue

        t0 = time.perf_counter()
        status, error, ttfb = 0, None, None

        try:
            if endpoint == "news_categories":
                status, error = await hit_news_categories(client, args.base_url)
            elif endpoint == "news_list":
                status, error = await hit_news_list(
                    client, args.base_url, random.choice(category_ids)
                )
            elif endpoint == "news_detail":
                status, error = await hit_news_detail(
                    client, args.base_url, random.choice(news_ids)
                )
            elif endpoint == "login":
                status, error = await hit_login(client, args.base_url)
            elif endpoint == "chat_stream":
                token = random.choice(tokens)
                sid_list = sessions.get(token) or [0]
                status, error, ttfb = await hit_chat_stream(
                    client, args.base_url, token, random.choice(sid_list)
                )
        except httpx.TimeoutException:
            status, error = 0, "timeout"
        except httpx.ConnectError:
            status, error = 0, "connection refused"
        except Exception as e:                                  # noqa: BLE001
            status, error = 0, f"{type(e).__name__}: {e}"

        latency = (time.perf_counter() - t0) * 1000
        stats.record(
            endpoint=endpoint,
            latency_ms=latency,
            ok=(status == 200),
            status=status,
            ttfb_ms=ttfb,
            error=error,
        )


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
async def run(args: argparse.Namespace) -> Stats:
    stats = Stats()
    limits = httpx.Limits(
        max_connections=args.users * 2, max_keepalive_connections=args.users
    )
    timeout = httpx.Timeout(args.timeout, connect=10.0)

    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        follow_redirects=True,
        # trust_env=False：不要读系统里的 HTTP_PROXY / HTTPS_PROXY。
        # 本机如果开着代理软件，localhost 请求会被代理截走并返回 502，
        # 看起来像"服务返回异常"，其实是压根没连上服务，极难排查。
        trust_env=False,
    ) as client:
        # 0) 探活
        try:
            r = await client.get(f"{args.base_url}/")
            print(f"[1/4] 服务探活成功：{r.status_code} {r.text[:60]}")
        except Exception as e:                                  # noqa: BLE001
            raise SystemExit(
                f"连不上 {args.base_url}（{type(e).__name__}: {e}）\n"
                f"请先启动服务：uvicorn main:app --reload"
            )
        if r.status_code != 200:
            raise SystemExit(
                f"{args.base_url} 返回了 {r.status_code}，这不像是我们的服务。\n"
                f"  1) 确认服务已启动：uvicorn main:app --reload\n"
                f"  2) 确认端口没被别的程序占用\n"
                f"  3) 若本机开了代理，关掉，或设 NO_PROXY=127.0.0.1,localhost"
            )

        # 1) 取真实分类 ID、新闻 ID 作为压测素材
        category_ids: list[int] = []
        news_ids: list[int] = []
        try:
            r = await client.get(f"{args.base_url}/api/news/categories")
            for c in (r.json().get("data") or []):
                if isinstance(c, dict) and c.get("id") is not None:
                    category_ids.append(int(c["id"]))
            if category_ids:
                r = await client.get(
                    f"{args.base_url}/api/news/list",
                    params={"categoryId": category_ids[0], "page": 1, "pageSize": 20},
                )
                for item in ((r.json().get("data") or {}).get("list") or []):
                    if isinstance(item, dict) and item.get("id") is not None:
                        news_ids.append(int(item["id"]))
        except Exception as e:                                  # noqa: BLE001
            print(f"  [警告] 取压测素材失败：{type(e).__name__}: {e}")
        print(f"[2/4] 压测素材：{len(category_ids)} 个分类，{len(news_ids)} 条新闻")
        if not category_ids:
            print("  [提示] 没有分类数据，news_list / news_detail 场景会被跳过")

        # 2) 准备账号与会话
        tokens: list[str] = []
        sessions: dict[str, list[int]] = {}
        need_auth = args.scenario in ("chat", "mixed", "login")
        if need_auth:
            tokens = await prepare_accounts(client, args.base_url, max(1, args.accounts))
            print(f"[3/4] 测试账号：{len(tokens)} 个")
            if args.scenario in ("chat", "mixed"):
                sessions = await prepare_sessions(
                    client, args.base_url, tokens, args.sessions_per_account
                )
                print(f"      预建会话：{sum(len(v) for v in sessions.values())} 个")
        else:
            print("[3/4] 无需鉴权，跳过账号准备")

        # 3) 起并发
        print(f"[4/4] 开始压测：{args.users} 并发 × {args.duration}s ...")
        stop_at = time.perf_counter() + args.duration
        await asyncio.gather(
            *[
                worker(
                    f"w{i}", client, args, stats, stop_at,
                    tokens, sessions, category_ids, news_ids,
                )
                for i in range(args.users)
            ]
        )
    return stats


def report(args: argparse.Namespace, stats: Stats, elapsed: float) -> dict:
    line = "=" * 66
    print()
    print(line)
    print(f"压测报告   场景={args.scenario}   并发={args.users}   时长={elapsed:.1f}s")
    print(line)

    ok_rate = stats.ok / stats.total * 100 if stats.total else 0.0
    print(f"{'总请求数':<22}{stats.total}")
    print(f"{'成功':<22}{stats.ok}  ({ok_rate:.1f}%)")
    print(f"{'失败':<22}{stats.failed}")
    print(f"{'限流命中 (429)':<22}{stats.ratelimited}")
    print(f"{'平均 QPS':<22}{stats.ok / elapsed:.1f}")
    print(f"{'平均延迟 (ms)':<22}{avg(stats.latencies):.1f}")
    if stats.latencies:
        print(
            f"{'延迟 P50/P90/P95/P99':<22}"
            f"{percentile(stats.latencies, .50):.1f} / "
            f"{percentile(stats.latencies, .90):.1f} / "
            f"{percentile(stats.latencies, .95):.1f} / "
            f"{percentile(stats.latencies, .99):.1f}"
        )
    if stats.ttfb:
        print(
            f"{'首字延迟 P50/P95 (ms)':<22}"
            f"{percentile(stats.ttfb, .50):.1f} / {percentile(stats.ttfb, .95):.1f}"
        )

    print()
    print("-" * 66)
    print(f"{'按接口分布':<20}{'请求':>8}{'成功':>8}{'429':>7}{'平均ms':>10}{'P95ms':>10}")
    print("-" * 66)
    for ep, s in sorted(stats.by_endpoint.items(), key=lambda kv: -kv[1]["n"]):
        print(
            f"{ep:<20}{s['n']:>8}{s['ok']:>8}{s['rl']:>7}"
            f"{avg(s['lat']):>10.1f}{percentile(s['lat'], .95):>10.1f}"
        )

    if stats.errors:
        print()
        print("-" * 66)
        print("失败明细（Top 10）")
        print("-" * 66)
        for msg, cnt in stats.errors.most_common(10):
            print(f"  {cnt:>6}  {msg}")

    print(line)

    return {
        "scenario": args.scenario,
        "users": args.users,
        "duration_sec": round(elapsed, 2),
        "total": stats.total,
        "ok": stats.ok,
        "failed": stats.failed,
        "ratelimited_429": stats.ratelimited,
        "qps": round(stats.ok / elapsed, 2) if elapsed else 0,
        "latency_avg_ms": round(avg(stats.latencies), 1),
        "latency_p50_ms": round(percentile(stats.latencies, .50), 1),
        "latency_p90_ms": round(percentile(stats.latencies, .90), 1),
        "latency_p95_ms": round(percentile(stats.latencies, .95), 1),
        "latency_p99_ms": round(percentile(stats.latencies, .99), 1),
        "ttfb_p50_ms": round(percentile(stats.ttfb, .50), 1) if stats.ttfb else None,
        "ttfb_p95_ms": round(percentile(stats.ttfb, .95), 1) if stats.ttfb else None,
        "by_endpoint": {
            ep: {
                "n": s["n"], "ok": s["ok"], "ratelimited": s["rl"], "failed": s["fail"],
                "latency_avg_ms": round(avg(s["lat"]), 1),
                "latency_p95_ms": round(percentile(s["lat"], .95), 1),
            }
            for ep, s in stats.by_endpoint.items()
        },
        "errors": dict(stats.errors.most_common(20)),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="今日新闻 App 接口压测")
    p.add_argument("--base-url", default="http://127.0.0.1:8000", help="服务地址")
    p.add_argument("--scenario", default="news",
                   choices=["news", "chat", "login", "mixed"], help="压测场景")
    p.add_argument("--users", type=int, default=20, help="并发协程数")
    p.add_argument("--duration", type=float, default=30.0, help="持续时间（秒）")
    p.add_argument("--accounts", type=int, default=5,
                   help="测试账号数；聊天限流是「每用户 20 次/分钟」，账号越多总配额越大")
    p.add_argument("--sessions-per-account", type=int, default=1, help="每账号预建会话数")
    p.add_argument("--timeout", type=float, default=60.0, help="单请求超时（秒）")
    p.add_argument("--out", default=None, help="报告输出路径，默认写到 text/ 下")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    print(f"目标：{args.base_url}\n场景：{args.scenario}\n")
    t0 = time.perf_counter()
    try:
        stats = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n已被用户中断")
        return
    elapsed = time.perf_counter() - t0

    result = report(args, stats, elapsed)

    out = args.out or (
        Path(__file__).parent
        / f"load_test_{args.scenario}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    Path(out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n明细已保存：{out}")
    print("把上面这几个数字抄进简历就行（QPS / P95 延迟 / 首字延迟）。")


if __name__ == "__main__":
    main()
