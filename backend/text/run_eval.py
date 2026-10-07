"""
评测集打分脚本 —— 跑一遍评测集，算出「要点命中率 / 编造率 / 拒答正确率 / 首字延迟」

用法
----
# 先跑通 3 条，确认接口和账号都没问题
python text/run_eval.py --max-items 3

# 全量跑（推荐 3 个账号，因为聊天接口限流是每用户 20 次/分钟）
python text/run_eval.py --accounts 3 --eval-file text/eval_set.json

# 只看某一类（比如专门盯时间限定题）
python text/run_eval.py --category time_bound

产出
----
- text/eval_report_<时间>.json  机器可读的明细
- text/eval_report_<时间>.md    可直接贴进笔记/简历附件的报告

打分口径（重要，先看懂再看数）
------------------------------
每条题目按三件事判定：
  1) 要点命中：expected_keywords 挡不住 expected_hit_ratio 就算挂
  2) 禁词违规：forbidden_keywords 出现任意一个直接判挂（时间限定题主要靠它抓"混入其他时间段"）
  3) 拒答正确性：should_refuse=true 却没拒答 → 判挂（这是编造的最强信号）
另外单独统计「疑似误拒」：不该拒答的题却出现拒答话术——这类不计挂，但会影响覆盖率。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import httpx

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 出现这些词，就认为模型在"如实说不知道"。
# 这是保守清单，宁可漏判也不要误判——误判会把"诚实的回答"算成拒答。
REFUSE_MARKERS = [
    "无法", "不能", "帮不了", "帮不上", "做不到", "暂无", "暂未",
    "未检索到", "没有权限", "不支持", "不方便", "没有找到", "没有相关",
    "未覆盖", "查不到", "没有查到",
]


# ----------------------------------------------------------------------
# 账号池：聊天接口有「每用户 20 次/分钟」限流，多账号轮换才能跑完全量
# ----------------------------------------------------------------------
class AccountPool:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self._i = 0

    def next(self) -> str:
        token = self.tokens[self._i % len(self.tokens)]
        self._i += 1
        return token


def rand_name(prefix: str) -> str:
    import random
    import string
    return f"{prefix}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"


async def prepare_accounts(client: httpx.AsyncClient, base_url: str, n: int) -> list[str]:
    tokens: list[str] = []
    for i in range(n):
        username = f"evaluser_{i:02d}"
        password = "evaluser123"
        r = await client.post(f"{base_url}/api/user/register",
                              json={"username": username, "password": password})
        if r.status_code != 200:
            r = await client.post(f"{base_url}/api/user/login",
                                  json={"username": username, "password": password})
        try:
            tokens.append(r.json()["data"]["token"])
        except Exception:                                   # noqa: BLE001
            print(f"  [警告] 账号 {username} 准备失败：HTTP {r.status_code}")
    if not tokens:
        raise SystemExit("没有可用账号，请确认服务已启动、MySQL 可连接")
    return tokens


async def create_session(client: httpx.AsyncClient, base_url: str, token: str) -> int | None:
    r = await client.post(f"{base_url}/api/chat/session", json={},
                          headers={"Authorization": f"Bearer {token}"})
    try:
        return r.json()["data"]["sessionId"]
    except Exception:                                       # noqa: BLE001
        return None


# ----------------------------------------------------------------------
# 调一次流式聊天，返回 (回答全文, 首字延迟ms, 总耗时ms, 错误)
# ----------------------------------------------------------------------
async def ask(
    client: httpx.AsyncClient, base_url: str, token: str, session_id: int, message: str
) -> tuple[str, float | None, float, str | None]:
    payload = {"sessionId": session_id, "message": message}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    t0 = time.perf_counter()
    ttfb: float | None = None
    parts: list[str] = []
    buf = ""

    async with client.stream("POST", f"{base_url}/api/chat/stream",
                             json=payload, headers=headers) as r:
        if r.status_code == 429:
            await r.aread()
            return "", None, (time.perf_counter() - t0) * 1000, "RATELIMIT"
        if r.status_code != 200:
            await r.aread()
            return "", None, (time.perf_counter() - t0) * 1000, f"HTTP {r.status_code}"

        try:
            async for chunk in r.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    raw, buf = buf.split("\n\n", 1)
                    for line in raw.splitlines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            evt = json.loads(line[5:].strip())
                        except Exception:                   # noqa: BLE001
                            continue
                        if evt.get("type") == "content":
                            if ttfb is None:
                                ttfb = (time.perf_counter() - t0) * 1000
                            parts.append(evt.get("content", ""))
                        elif evt.get("type") == "error":
                            return "".join(parts), ttfb, (time.perf_counter() - t0) * 1000, \
                                   evt.get("message", "SSE error")
        except httpx.RemoteProtocolError:
            pass

    return "".join(parts), ttfb, (time.perf_counter() - t0) * 1000, None


# ----------------------------------------------------------------------
# 打分
# ----------------------------------------------------------------------
def score(item: dict, answer: str) -> dict:
    expected = [k for k in (item.get("expected_keywords") or []) if k and "TODO" not in k]
    forbidden = [k for k in (item.get("forbidden_keywords") or []) if k and "TODO" not in k]
    need = float(item.get("expected_hit_ratio", 0.5))
    should_refuse = bool(item.get("should_refuse", False))

    hits = [k for k in expected if k in answer]
    ratio = len(hits) / len(expected) if expected else 1.0
    bad_words = [k for k in forbidden if k in answer]
    refused = any(m in answer for m in REFUSE_MARKERS)

    reasons: list[str] = []
    if bad_words:
        reasons.append(f"出现禁词 {bad_words}")
    if expected and ratio < need:
        reasons.append(f"要点命中 {len(hits)}/{len(expected)}，低于阈值 {need}")
    if should_refuse and not refused:
        reasons.append("该拒答却没拒答（编造风险最高的一类）")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "keyword_hits": hits,
        "keyword_total": len(expected),
        "keyword_ratio": round(ratio, 3),
        "forbidden_hits": bad_words,
        "refused": refused,
        "false_refuse": (not should_refuse) and refused and bool(expected),
    }


# ----------------------------------------------------------------------
# 跑一条
# ----------------------------------------------------------------------
async def run_item(
    client: httpx.AsyncClient, args: argparse.Namespace, pool: AccountPool, item: dict
) -> dict:
    turns = item.get("turns") or []
    idx = item.get("answer_index", len(turns) - 1)

    result = {
        "id": item.get("id"),
        "category": item.get("category"),
        "question": turns[idx] if turns else "",
        "turns": turns,
        "answer": "",
        "ttfb_ms": None,
        "latency_ms": None,
        "error": None,
        "passed": False,
        "reasons": [],
        "notes": item.get("notes", ""),
    }
    if not turns:
        result["error"] = "题目为空"
        result["reasons"] = ["题目为空"]
        return result

    # 整条用例兜底：网络层异常（服务重启、连接被重置、读超时……）只让这一条失败，
    # 绝不能让整个批次崩掉 —— 一条题异常导致后面几十条全白跑，是评测脚本最蠢的失败方式。
    # 实测踩过：服务中途重启，httpx.ReadError 直接冒泡，跑到第 2 条整个脚本就退出了。
    try:
        # 每条题独立开会话，避免上一条的历史污染下一条
        token = pool.next()
        session_id = await create_session(client, args.base_url, token)
        if session_id is None:
            result["error"] = "建会话失败"
            result["reasons"] = ["建会话失败"]
            return result

        answer = ""
        err: str | None = None
        ttfb: float | None = None
        latency = 0.0

        for i, msg in enumerate(turns):
            for attempt in range(args.max_retry + 1):
                answer, ttfb, latency, err = await ask(
                    client, args.base_url, token, session_id, msg
                )
                if err != "RATELIMIT":
                    break
                # 被限流：换个账号，歇一会再试（限流窗口是 60 秒）
                wait = args.ratelimit_wait
                print(f"      限流，等 {wait}s 后换账号重试（第 {attempt + 1} 次）")
                await asyncio.sleep(wait)
                token = pool.next()
                session_id = await create_session(client, args.base_url, token)
                if session_id is None:
                    break

            if err and err != "RATELIMIT":
                result["error"] = err
                result["reasons"] = [f"接口错误：{err}"]
                return result

            if err == "RATELIMIT":
                # 走到这里说明重试次数已经用完还是被限流
                result["error"] = "限流重试耗尽"
                result["reasons"] = [
                    f"连续 {args.max_retry + 1} 次都被限流。"
                    f"加大 --accounts，或临时调大 cache/chat_cache.py 里的 RATELIMIT_MAX"
                ]
                return result

            if i == idx:
                result["answer"] = answer
                result["ttfb_ms"] = round(ttfb, 1) if ttfb else None
                result["latency_ms"] = round(latency, 1)

        if not result["answer"]:
            result["error"] = result["error"] or "回答为空"
            result["reasons"] = ["回答为空"]
            return result

        result.update(score(item, result["answer"]))
        return result

    except Exception as e:                                  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        result["reasons"] = [f"请求异常：{result['error']}"]
        return result


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------
def p95(values: list[float]) -> float:
    """
    取 P95（最近邻法）。
    注意别写成 int(n*0.95)-1：样本少的时候那个索引会落到 0，
    算出比 P50 还小的"P95"，报告一眼就假。
    """
    s = sorted(values)
    idx = min(len(s) - 1, max(0, math.ceil(len(s) * 0.95) - 1))
    return s[idx]


def build_report(args: argparse.Namespace, results: list[dict], elapsed: float) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    scored = [r for r in results if r.get("answer")]

    ratios = [r["keyword_ratio"] for r in scored if r["keyword_total"] > 0]
    refusals = [r for r in results if r.get("category") == "out_of_scope"]
    refuse_ok = sum(1 for r in refusals if r.get("refused"))
    false_refuse = sum(1 for r in results if r.get("false_refuse"))
    violations = sum(len(r.get("forbidden_hits") or []) for r in results)
    ttfbs = [r["ttfb_ms"] for r in scored if r.get("ttfb_ms")]
    latencies = [r["latency_ms"] for r in scored if r.get("latency_ms")]

    by_cat: dict[str, dict] = defaultdict(lambda: {"n": 0, "ok": 0, "ratios": []})
    for r in results:
        c = r.get("category") or "unknown"
        by_cat[c]["n"] += 1
        if r["passed"]:
            by_cat[c]["ok"] += 1
        if r.get("keyword_total"):
            by_cat[c]["ratios"].append(r["keyword_ratio"])

    return {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": args.base_url,
        "eval_file": str(args.eval_file),
        "elapsed_sec": round(elapsed, 1),
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "avg_keyword_ratio": round(statistics.mean(ratios) * 100, 1) if ratios else None,
        "refuse_total": len(refusals),
        "refuse_correct": refuse_ok,
        "refuse_rate": round(refuse_ok / len(refusals) * 100, 1) if refusals else None,
        "forbidden_violations": violations,
        "false_refuse": false_refuse,
        "ttfb_p50_ms": round(statistics.median(ttfbs), 1) if ttfbs else None,
        "ttfb_p95_ms": round(p95(ttfbs), 1) if ttfbs else None,
        "latency_avg_ms": round(statistics.mean(latencies), 1) if latencies else None,
        "by_category": {
            c: {
                "n": v["n"],
                "passed": v["ok"],
                "pass_rate": round(v["ok"] / v["n"] * 100, 1) if v["n"] else 0.0,
                "avg_keyword_ratio": (
                    round(statistics.mean(v["ratios"]) * 100, 1) if v["ratios"] else None
                ),
            }
            for c, v in sorted(by_cat.items())
        },
        "results": results,
    }


def cell(text: str, width: int = 40) -> str:
    """表格单元格里不能有竖线，顺便截断太长的问题"""
    t = (text or "").replace("|", "/").replace("\n", " ").strip()
    return t[:width] + ("…" if len(t) > width else "")


def print_and_save_md(rep: dict, md_path: Path) -> None:
    L = "=" * 70
    print()
    print(L)
    print(f"评测报告   {rep['run_at']}   用时 {rep['elapsed_sec']}s")
    print(L)
    print(f"{'评测集':<20}{rep['eval_file']}")
    print(f"{'条目数':<20}{rep['total']}")
    print(f"{'通过':<20}{rep['passed']}  ({rep['pass_rate']}%)")
    print(f"{'平均要点命中率':<20}{rep['avg_keyword_ratio']}%")
    print(f"{'禁词违规次数':<20}{rep['forbidden_violations']}")
    if rep["refuse_total"]:
        print(f"{'拒答正确率':<20}{rep['refuse_correct']}/{rep['refuse_total']}"
              f"  ({rep['refuse_rate']}%)")
    print(f"{'疑似误拒':<20}{rep['false_refuse']}")
    if rep["ttfb_p50_ms"]:
        print(f"{'首字延迟 P50/P95':<20}{rep['ttfb_p50_ms']} / {rep['ttfb_p95_ms']} ms")
    if rep["latency_avg_ms"]:
        print(f"{'平均回答耗时':<20}{rep['latency_avg_ms']} ms")

    print()
    print("-" * 70)
    print(f"{'分类别表现':<18}{'条数':>6}{'通过':>6}{'通过率':>10}{'要点命中':>12}")
    print("-" * 70)
    for c, v in rep["by_category"].items():
        print(f"{c:<18}{v['n']:>6}{v['passed']:>6}{v['pass_rate']:>9}%"
              f"{(str(v['avg_keyword_ratio']) + '%'):>12}")

    failures = [r for r in rep["results"] if not r["passed"]]
    if failures:
        print()
        print("-" * 70)
        print(f"未通过明细（{len(failures)} 条）")
        print("-" * 70)
        for r in failures:
            print(f"\n[{r['id']}] {r['category']}")
            print(f"  问：{cell(r['question'], 60)}")
            print(f"  原因：{'；'.join(r['reasons'])}")
            print(f"  回答：{cell(r['answer'], 100)}")

    print()
    print(L)
    print(f"JSON 报告：{md_path.with_suffix('.json')}")
    print(f"Markdown 报告：{md_path}")

    # ---- 写 Markdown ----
    lines: list[str] = []
    lines.append("# 智能问答系统评测报告\n")
    lines.append(f"- 运行时间：{rep['run_at']}")
    lines.append(f"- 评测集：`{rep['eval_file']}`，共 {rep['total']} 条")
    lines.append(f"- 服务地址：{rep['base_url']}")
    lines.append(f"- 总用时：{rep['elapsed_sec']} 秒\n")

    lines.append("## 总览\n")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 通过率 | {rep['passed']}/{rep['total']}（{rep['pass_rate']}%） |")
    lines.append(f"| 平均要点命中率 | {rep['avg_keyword_ratio']}% |")
    lines.append(f"| 禁词违规次数 | {rep['forbidden_violations']} |")
    if rep["refuse_total"]:
        lines.append(f"| 拒答正确率 | {rep['refuse_correct']}/{rep['refuse_total']}"
                     f"（{rep['refuse_rate']}%） |")
    lines.append(f"| 疑似误拒 | {rep['false_refuse']} |")
    if rep["ttfb_p50_ms"]:
        lines.append(f"| 首字延迟 P50 / P95 | {rep['ttfb_p50_ms']} / {rep['ttfb_p95_ms']} ms |")
    if rep["latency_avg_ms"]:
        lines.append(f"| 平均回答耗时 | {rep['latency_avg_ms']} ms |")
    lines.append("")

    lines.append("## 分类别表现\n")
    lines.append("| 类别 | 条数 | 通过 | 通过率 | 平均要点命中率 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for c, v in rep["by_category"].items():
        lines.append(f"| {c} | {v['n']} | {v['passed']} | {v['pass_rate']}% | "
                     f"{v['avg_keyword_ratio']}% |")
    lines.append("")

    if failures:
        lines.append(f"## 未通过明细（{len(failures)} 条）\n")
        lines.append("| ID | 类别 | 问题 | 未通过原因 |")
        lines.append("| --- | --- | --- | --- |")
        for r in failures:
            lines.append(
                f"| {r['id']} | {r['category']} | {cell(r['question'], 40)} | "
                f"{cell('；'.join(r['reasons']), 60)} |"
            )
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")


# ----------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="评测集打分")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--eval-file", default=None, help="评测集路径，默认 text/eval_set.json")
    p.add_argument("--accounts", type=int, default=3, help="测试账号数（绕开 20 次/分钟限流）")
    p.add_argument("--max-items", type=int, default=None, help="只跑前 N 条")
    p.add_argument("--category", default=None, help="只跑某一类")
    p.add_argument("--max-retry", type=int, default=3, help="遇限流最多重试几次")
    p.add_argument("--ratelimit-wait", type=float, default=20.0, help="遇限流等待秒数")
    p.add_argument("--abort-after", type=int, default=3,
                   help="连续多少条都是请求异常就中止（默认 3），避免服务挂了还在空跑")
    p.add_argument("--out", default=None, help="报告输出前缀（默认 text/eval_report_<时间>）")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    args.eval_file = Path(args.eval_file) if args.eval_file else Path(__file__).parent / "eval_set.json"

    if not args.eval_file.exists():
        raise SystemExit(
            f"找不到评测集：{args.eval_file}\n"
            f"先跑 python text/build_eval_set.py --limit 15 --use-llm 生成草稿"
        )

    data = json.loads(args.eval_file.read_text(encoding="utf-8"))
    items = data["items"] if isinstance(data, dict) else data

    if args.category:
        items = [i for i in items if i.get("category") == args.category]
    if args.max_items:
        items = items[: args.max_items]

    # 提示哪些条目还没填
    todo = [i["id"] for i in items
            if any("TODO" in str(t) for t in (i.get("turns") or []))]
    if todo:
        print(f"[提示] 有 {len(todo)} 条题目还是 TODO 占位：{todo[:5]}"
              f"{' ...' if len(todo) > 5 else ''}\n"
              f"       这些条目仍会跑，但要点命中率没有意义（关键字被自动忽略）。\n")

    if not items:
        raise SystemExit("没有匹配的条目")

    print(f"评测集：{args.eval_file}")
    print(f"待跑条目：{len(items)} 条")
    print(f"账号数：{args.accounts}（聊天限流 20 次/分钟/用户）\n")

    timeout = httpx.Timeout(120.0, connect=10.0)
    t0 = time.perf_counter()

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        # 别读系统代理：本机开了代理软件时，localhost 请求会被截走并返回 502
        trust_env=False,
    ) as client:
        try:
            r = await client.get(f"{args.base_url}/")
        except Exception as e:                              # noqa: BLE001
            raise SystemExit(f"连不上 {args.base_url}：{type(e).__name__}: {e}\n"
                             f"请先启动服务：uvicorn main:app --reload")
        if r.status_code != 200:
            raise SystemExit(
                f"{args.base_url} 返回 {r.status_code}，不像是我们的服务。\n"
                f"  1) 确认服务已启动  2) 确认端口没被占用  "
                f"3) 若本机开了代理，设 NO_PROXY=127.0.0.1,localhost"
            )

        tokens = await prepare_accounts(client, args.base_url, max(1, args.accounts))
        print(f"账号就绪：{len(tokens)} 个\n")
        pool = AccountPool(tokens)

        results: list[dict] = []
        consecutive_error = 0
        for i, item in enumerate(items, start=1):
            q = (item.get("turns") or [""])[item.get("answer_index", len(item.get("turns") or [""]) - 1)]
            print(f"[{i}/{len(items)}] {item.get('id')} ({item.get('category')})  {q[:40]}")
            r = await run_item(client, args, pool, item)
            results.append(r)
            flag = "通过" if r["passed"] else "未通过"
            print(f"      → {flag}"
                  f"{'  ' + '；'.join(r['reasons']) if r['reasons'] else ''}")

            # 连续多条都是「请求层异常」（而不是答错），基本可以判定服务已经挂了。
            # 这时继续跑没有意义，只会刷一屏同样的错误、白等一整天。
            consecutive_error = consecutive_error + 1 if r.get("error") else 0
            if consecutive_error >= args.abort_after:
                print(
                    f"\n[中止] 连续 {consecutive_error} 条都是请求异常，服务可能已经挂了。"
                    f"剩下的 {len(items) - i} 条不再执行。\n"
                    f"       先确认服务还在跑：curl {args.base_url}/"
                )
                break

    elapsed = time.perf_counter() - t0
    rep = build_report(args, results, elapsed)

    prefix = Path(args.out) if args.out else (
        Path(__file__).parent / f"eval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    rep_path = prefix.with_suffix(".json")
    rep_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print_and_save_md(rep, prefix.with_suffix(".md"))


if __name__ == "__main__":
    asyncio.run(main())
