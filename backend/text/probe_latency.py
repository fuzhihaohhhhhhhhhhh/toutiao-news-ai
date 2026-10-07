"""
延迟诊断探针 —— 定位「接口很慢」到底慢在哪一段

为什么需要它
------------
「接口慢」是最难查的一类问题，因为可能的原因太多：网络、代理、数据库、缓存、
向量检索、模型本身、编排链路…… 瞎猜很浪费时间。这个脚本把链路拆成四段分别计时，
一次跑完就能看出瓶颈在哪一层。

用法
----
    python text/probe_latency.py              # 全跑
    python text/probe_latency.py --quick      # 只跑第 1、2 段（不调大模型，最省）

四段测什么
----------
  1. 裸网络：同一进程内对照「走系统代理 / 不走代理」，直接排除代理干扰
  2. 单组件：嵌入、向量检索各自要多久
  3. 模型行为：短 prompt vs 真实长度 prompt 的首字延迟，并统计「空 content 的 chunk 数」
     —— 这个数字是关键：如果空 chunk 很多，说明模型在做「思考（reasoning）」，
        用户看到的首字延迟 = 思考时间，而不是网络慢
  4. 模型对比：当前模型 / 关掉思考 / 换更快的模型，给出可选的替代配置

本项目实测结论（2026-09-20）
---------------------------
- 网络、代理、Redis、嵌入、检索全部正常（都在 1 秒内）
- 根因是 `qwen3.8-max` 带思考模式：真实 prompt 下首字 5~12 秒，采样到过 30 秒
- 关掉思考（enable_thinking=False）后空 chunk 从 44 个降到 4 个，首字砍掉一半
- 换 qwen-plus 首字只要 0.7 秒
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx                                                              # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage           # noqa: E402
from langchain_openai import ChatOpenAI                                   # noqa: E402

from models.factory import api_key, base_url                              # noqa: E402
from utils.config_handler import rag_conf                                 # noqa: E402

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

DASHSCOPE_PROBE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/models"

# 用来代替真实检索结果的长 prompt，长度和线上一致（约 1400 字符）
LONG_PROMPT = (
    "你是新闻助手。以下是检索到的新闻资料，请基于它回答用户问题，只准使用资料里的信息。\n\n"
    "新闻资料：\n"
    "【发布时间：2023-09-09 11:15:00】标题：全球首个虚拟现实新闻平台\n"
    "简介：沉浸式报道重大事件\n正文：BBC推出全球首个虚拟现实新闻平台，用户可以身临其境体验新闻现场。\n\n"
    "【发布时间：2023-08-28 12:20:00】标题：Meta发布AI虚拟主播\n"
    "简介：实时生成新闻播报\n正文：Meta推出AI虚拟主播，可实时生成多语种新闻播报内容。\n\n"
    "【发布时间：2023-08-23 08:20:00】标题：苹果发布AI芯片M3\n"
    "简介：性能提升40%\n正文：苹果宣布推出M3系列芯片，AI性能提升40%。\n\n"
    "### 当天日期\n2026-09-20\n"
)
SHORT_PROMPT = "说一个字"
USER_QUESTION = "今天有什么科技新闻？"


# ----------------------------------------------------------------------
# 1. 裸网络
# ----------------------------------------------------------------------
async def part_network(quick: bool) -> None:
    print("=== 1. 裸网络：到 dashscope 的连通性（同一进程内做对照）===")
    rounds = 1 if quick else 2
    for label, trust_env in (("走系统代理", True), ("不走代理  ", False)):
        for i in range(rounds):
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=60, trust_env=trust_env) as c:
                    r = await c.get(DASHSCOPE_PROBE_URL)
                print(f"  {label} 第{i + 1}次: HTTP {r.status_code}   "
                      f"耗时 {(time.perf_counter() - t0) * 1000:8.0f} ms")
            except Exception as e:                                        # noqa: BLE001
                print(f"  {label} 第{i + 1}次: {type(e).__name__}: {e}   "
                      f"耗时 {(time.perf_counter() - t0) * 1000:8.0f} ms")
    print("  说明：两行数字接近 → 代理不是瓶颈；差很多 → 代理在拖后腿")


# ----------------------------------------------------------------------
# 2. 单组件
# ----------------------------------------------------------------------
async def part_components(quick: bool) -> None:
    rounds = 1 if quick else 2

    print("\n=== 2a. 嵌入模型 ===")
    try:
        from models.factory import get_embed_model
        emb = get_embed_model()
        for i in range(rounds):
            t0 = time.perf_counter()
            emb.embed_query(f"今天有什么科技新闻 {i}")
            print(f"  第{i + 1}次: {(time.perf_counter() - t0) * 1000:8.0f} ms")
    except Exception as e:                                                # noqa: BLE001
        print(f"  {type(e).__name__}: {e}")

    print("\n=== 2b. 向量检索（同步调用，注意它会阻塞事件循环）===")
    try:
        from rag.retriever import RetrieverService
        retriever = RetrieverService()
        for i in range(rounds):
            t0 = time.perf_counter()
            docs = retriever.search(f"科技新闻 {i}")
            print(f"  第{i + 1}次: 命中 {len(docs):>2} 条   "
                  f"耗时 {(time.perf_counter() - t0) * 1000:8.0f} ms")
        if docs:
            times = [str(d.metadata.get("publish_time", "")) for d in docs]
            print(f"  命中内容的最新发布时间: {max(times)}")
            print("  说明：如果这里明显早于数据库里的最新新闻，说明向量库是旧的，需要重建")
    except Exception as e:                                                # noqa: BLE001
        print(f"  {type(e).__name__}: {e}")


# ----------------------------------------------------------------------
# 3. 模型行为：有没有在「思考」
# ----------------------------------------------------------------------
def build_model(model: str | None = None, **extra) -> ChatOpenAI:
    return ChatOpenAI(
        model=model or rag_conf["chat_model_name"],
        api_key=api_key, base_url=base_url, **extra,
    )


async def measure(label: str, llm: ChatOpenAI, system_prompt: str) -> None:
    try:
        t0 = time.perf_counter()
        first_any: float | None = None
        first_text: float | None = None
        empty_chunks = 0
        text_len = 0

        async for chunk in llm.astream([
            SystemMessage(content=system_prompt),
            HumanMessage(content=USER_QUESTION),
        ]):
            elapsed = (time.perf_counter() - t0) * 1000
            if first_any is None:
                first_any = elapsed
            text = chunk.content if isinstance(chunk.content, str) else ""
            if text:
                text_len += len(text)
                if first_text is None:
                    first_text = elapsed
            else:
                empty_chunks += 1

        print(f"\n  【{label}】")
        print(f"    首个 chunk（哪怕是空的）  {first_any or 0:9.0f} ms")
        print(f"    首个有文字的 chunk        {first_text or 0:9.0f} ms   ← 用户实际感受到的首字延迟")
        print(f"    全部完成                  {(time.perf_counter() - t0) * 1000:9.0f} ms")
        print(f"    空 content 的 chunk 数    {empty_chunks}")
        print(f"    正文长度                  {text_len} 字符")
        if empty_chunks > 10:
            print("    ⚠️ 空 chunk 很多 → 模型在「思考（reasoning）」，首字延迟基本等于思考时间")
    except Exception as e:                                                # noqa: BLE001
        print(f"\n  【{label}】 失败: {type(e).__name__}: {e}")


async def part_model() -> None:
    print("\n=== 3. 模型行为：短 prompt 对比 ===")
    await measure("短 prompt（排除长度因素）", build_model(), SHORT_PROMPT)

    print("\n=== 4. 长 prompt（线上真实长度）下的模型对比 ===")
    await measure("当前配置：" + str(rag_conf["chat_model_name"]), build_model(), LONG_PROMPT)
    await measure("关掉思考 enable_thinking=False",
                  build_model(extra_body={"enable_thinking": False}), LONG_PROMPT)
    await measure("换 qwen-plus", build_model(model="qwen-plus"), LONG_PROMPT)
    await measure("换 qwen-turbo", build_model(model="qwen-turbo"), LONG_PROMPT)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="延迟诊断")
    p.add_argument("--quick", action="store_true",
                   help="只跑网络和单组件两段，不调大模型（最省）")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    print("=== 代理环境变量 ===")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                 "NO_PROXY", "no_proxy"):
        print(f"  {name:<13} = {os.getenv(name)}")

    await part_network(args.quick)
    await part_components(args.quick)
    if not args.quick:
        await part_model()


if __name__ == "__main__":
    asyncio.run(main())
