"""
冒烟测试（pytest 版）—— 给 PyCharm 的绿色三角用

为什么单独写这一份
------------------
load_test.py / run_eval.py 是「工具」，要在命令行带参数跑；
这一份是「测试」，每个用例都能在 PyCharm 里单独点绿色三角运行/调试。

三个设计原则
------------
1. 服务没起时自动 **skip**，而不是甩一堆看不懂的报错。
2. 默认只做不烧钱的检查；真正会调用大模型的用例由环境变量 RUN_LLM_TESTS 控制。
3. 每个用例彼此独立，可以单独跑，不依赖执行顺序。

在 PyCharm 里怎么用
-------------------
- 右键本文件 → Run 'pytest in test_smoke.py'，全部跑一遍
- 或者直接点某个用例左边的绿色三角，只跑那一个
- 想让它断在某个地方：在用例里打断点，然后点「虫子」图标（Debug）
- 想把会调大模型的用例也跑起来：Run Configuration 里加环境变量 RUN_LLM_TESTS=1

命令行等价写法
--------------
    python -m pytest text/test_smoke.py -v
    python -m pytest text/test_smoke.py -v -k news          # 只跑和新闻相关的
    RUN_LLM_TESTS=1 python -m pytest text/test_smoke.py -v   # 连大模型用例一起跑
"""

from __future__ import annotations

import json
import os
import random
import string
import sys
import time
from pathlib import Path

import httpx
import pytest

# 保证无论从哪个工作目录启动 pytest，都能 import 到项目自己的模块（config / models）。
# test_20 需要直接查数据库，绕开 Redis 缓存，所以必须能 import 项目的 ORM。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
RUN_LLM_TESTS = os.getenv("RUN_LLM_TESTS", "0") == "1"
TIMEOUT = 30.0

skip_llm = pytest.mark.skipif(
    not RUN_LLM_TESTS,
    reason="会真实调用大模型（消耗额度、约 3~10 秒）。想跑就设环境变量 RUN_LLM_TESTS=1",
)


# ======================================================================
# 夹具
# ======================================================================
@pytest.fixture(scope="session")
def client():
    """
    全测试共用一个 httpx 客户端。
    连不上服务就直接 skip 掉整个文件——本地没起服务时不该看到一片红。

    trust_env=False：不读系统里的 HTTP_PROXY / HTTPS_PROXY。
    本机开着代理软件时，请求 localhost 会被代理截走并返回 502，
    导致"服务没启动"被误判成"服务返回 502"，排查起来很费时间。
    """
    with httpx.Client(
        base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=True, trust_env=False
    ) as c:
        try:
            r = c.get("/")
        except Exception as e:                              # noqa: BLE001
            pytest.skip(
                f"连不上 {BASE_URL}（{type(e).__name__}）——"
                f"请先启动服务：uvicorn main:app --reload"
            )
        if r.status_code != 200:
            pytest.skip(
                f"{BASE_URL} 返回 {r.status_code}，这不像是我们的服务。"
                f"常见原因：服务没启动，或本机代理把 localhost 请求截走了。"
            )
        yield c


def _rand(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _login_or_register(client: httpx.Client, username: str, password: str) -> str:
    """注册或登录，返回 token"""
    r = client.post("/api/user/register", json={"username": username, "password": password})
    if r.status_code != 200:
        r = client.post("/api/user/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"账号准备失败：{r.status_code} {r.text[:200]}"
    return r.json()["data"]["token"]


@pytest.fixture(scope="session")
def token(client) -> str:
    """主测试账号的 token"""
    return _login_or_register(client, "smoke_main", "smoke123456")


@pytest.fixture(scope="session")
def other_token(client) -> str:
    """第二个账号，专门用来测「越权访问别人的会话」"""
    return _login_or_register(client, "smoke_other", "smoke123456")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_session(client: httpx.Client, token: str) -> int:
    r = client.post("/api/chat/session", json={}, headers=_auth(token))
    assert r.status_code == 200, f"建会话失败：{r.status_code} {r.text[:200]}"
    return r.json()["data"]["sessionId"]


def _chat(client: httpx.Client, token: str, session_id: int, message: str) -> str:
    """发一条消息，把 SSE 流拼成完整回复返回"""
    r = client.post(
        "/api/chat/stream",
        json={"sessionId": session_id, "message": message},
        headers=_auth(token),
    )
    assert r.status_code == 200, f"聊天接口返回 {r.status_code}：{r.text[:200]}"

    parts: list[str] = []
    for line in r.text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            evt = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "content":
            parts.append(evt.get("content", ""))
    return "".join(parts)


def _count_messages_in_db(session_id: int) -> int:
    """
    直接数 PostgreSQL 里该会话的消息条数 —— 绕开 Redis 缓存。

    为什么不能走 HTTP 接口：
    GET /api/chat/session/{id}/messages 是「先读缓存、未命中才查库」。
    而越权删除失败时（返回 404），缓存清理那一步不会被执行，
    接口就会从缓存里把旧消息原样返回，让用例「假通过」。
    这个坑实测踩过：接口说消息还在，PG 里其实已经被删光了。
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from config.pg_config import PG_DATABASE_URL

    async def _count() -> int:
        # 每次现建引擎 + NullPool（不复用连接），用完立刻 dispose。
        # 不这么做的话，连接会在事件循环关闭之后才被回收，在 Windows 的
        # Proactor 事件循环上会抛：
        #   AttributeError: 'NoneType' object has no attribute 'send'
        engine = create_async_engine(PG_DATABASE_URL, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT count(*) FROM chat_message WHERE session_id = :sid"),
                    {"sid": session_id},
                )
                return int(result.scalar() or 0)
        finally:
            await engine.dispose()
            # 留一点时间让循环跑完连接关闭回调，再允许循环关闭
            await asyncio.sleep(0.25)

    return asyncio.run(_count())


# ======================================================================
# 一、基础连通性
# ======================================================================
def test_01_服务存活(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "message" in r.json()


def test_02_新闻分类接口(client):
    r = client.get("/api/news/categories")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    assert isinstance(body["data"], list), "data 应该是列表"
    if not body["data"]:
        pytest.skip("分类表是空的，先把测试数据灌进数据库")


# ======================================================================
# 二、新闻模块
# ======================================================================
def test_03_新闻列表分页字段齐全(client):
    cats = client.get("/api/news/categories").json()["data"]
    if not cats:
        pytest.skip("没有分类数据")
    cid = cats[0]["id"]

    r = client.get("/api/news/list", params={"categoryId": cid, "page": 1, "pageSize": 5})
    assert r.status_code == 200
    data = r.json()["data"]
    for field in ("list", "total", "hasMore"):
        assert field in data, f"分页响应里缺字段 {field}"


def test_04_新闻详情返回完整结构(client):
    cats = client.get("/api/news/categories").json()["data"]
    if not cats:
        pytest.skip("没有分类数据")
    lst = client.get(
        "/api/news/list", params={"categoryId": cats[0]["id"], "page": 1, "pageSize": 1}
    ).json()["data"]["list"]
    if not lst:
        pytest.skip("该分类下没有新闻")

    r = client.get("/api/news/detail", params={"id": lst[0]["id"]})
    assert r.status_code == 200
    data = r.json()["data"]
    for field in ("id", "title", "content", "publishTime", "categoryId", "views"):
        assert field in data, f"详情响应里缺字段 {field}"


def test_05_不存在的新闻应返回404(client):
    """
    这条是用例里最有价值的一条。

    如果这里红了、且实际状态码是 500，说明 routers/news.py 第 1 行
    导错了异常类：
        from http.client import HTTPException      ← 标准库的，不接受关键字参数
    应该改成：
        from fastapi import HTTPException
    后果：本该提示「新闻不存在」的地方，实际会抛
        TypeError: HTTPException() takes no keyword arguments
    变成一个 500 内部错误。
    """
    r = client.get("/api/news/detail", params={"id": 999999999})
    assert r.status_code == 404, (
        f"期望 404，实际 {r.status_code}。\n"
        f"若是 500，请检查 routers/news.py 第 1 行的 HTTPException 是从哪导入的。\n"
        f"响应内容：{r.text[:300]}"
    )


def test_05b_中文数据没有乱码(client):
    """
    防止 MySQL 字符集错误把中文存成双重编码。

    这个坑实际踩过：官方 mysql 镜像没有设置 locale，容器**首次**启动导入
    docs/sql/database.sql 时，镜像里的 mysql 客户端把字符集判成了 latin1，
    于是 UTF-8 的中文字节被当 latin1 解释、再由服务端转成 utf8mb4 存起来。

    实测（HEX 判定，无编码歧义）：
        正确  "头条" -> E5A4B4E69DA1        CHAR_LENGTH = 2
        乱码  "头条" -> C3A5C2A4C2B4…       CHAR_LENGTH = 6
        新闻标题：正确的 15 字坏成了 37 字

    修法见 docker/mysql-charset.cnf（强制 mysql 客户端使用 utf8mb4），
    对应 docker-compose.yml 里 mysql 服务的那条挂载。

    编号插在 05 之后、写成 05b，是为了不打乱后面 06~20 的既有编号。
    """
    r = client.get("/api/news/categories")
    assert r.status_code == 200, f"分类接口返回 {r.status_code}"
    names = [c["name"] for c in r.json()["data"]]
    assert names, "分类列表为空"

    # 分类名都是 2 个汉字；双重编码后会膨胀成 6 个字符
    overlong = [n for n in names if len(n) > 4]
    assert not overlong, (
        f"分类名长度异常，疑似字符集双重编码：{overlong}\n"
        f"完整列表：{names}\n"
        f"排查顺序：\n"
        f"  ① 看 docs/sql/database.sql 顶部的 `SET NAMES utf8mb4;` 是否还在；\n"
        f"  ② 若数据库已是对的、但接口仍乱码 —— 多半是 Redis 里留着修好之前写入的旧缓存，\n"
        f"     执行 `docker compose exec redis redis-cli FLUSHDB` 后重试"
    )
    assert "头条" in names, f"分类里找不到「头条」，实际是：{names}"

    # 新闻标题同理：双重编码会让长度膨胀约 2.5 倍
    r = client.get("/api/news/list", params={"categoryId": 1, "page": 1, "pageSize": 3})
    assert r.status_code == 200
    titles = [n["title"] for n in r.json()["data"]["list"]]
    assert titles, "该分类下没有新闻，无法校验中文"
    longest = max(titles, key=len)
    assert len(longest) < 60, (
        f"新闻标题异常长（{len(longest)} 字），疑似字符集双重编码。\n"
        f"最长的一条：{longest[:80]}"
    )


# ======================================================================
# 三、用户与鉴权
# ======================================================================
def test_06_登录成功返回token(client):
    _login_or_register(client, "smoke_login", "smoke123456")
    r = client.post(
        "/api/user/login", json={"username": "smoke_login", "password": "smoke123456"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["token"]


def test_07_密码错误应返回401(client):
    r = client.post(
        "/api/user/login", json={"username": "smoke_login", "password": "definitely_wrong"}
    )
    assert r.status_code == 401


def test_08_受保护接口缺token应被拒绝(client):
    r = client.get("/api/user/info")
    # 当前实现缺 Authorization 头会被框架的参数校验拦成 422；
    # 严格来说更该返回 401，这里两者都放过，但把差异记下来
    assert r.status_code in (401, 422), f"实际 {r.status_code}"


def test_09_带正确token能取到用户信息(client, token):
    r = client.get("/api/user/info", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["data"]["username"] == "smoke_main"


# ======================================================================
# 四、会话管理
# ======================================================================
def test_10_新建会话后能在列表里找到(client, token):
    sid = _new_session(client, token)
    r = client.get("/api/chat/sessions", headers=_auth(token))
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()["data"]["list"]]
    assert sid in ids, "新建的会话没出现在列表里"


def test_11_会话列表字段是驼峰命名(client, token):
    """前端按 createdAt / updatedAt 取值，字段名错了前端会拿到 undefined"""
    sessions = client.get("/api/chat/sessions", headers=_auth(token)).json()["data"]["list"]
    if not sessions:
        pytest.skip("当前账号没有会话")
    for field in ("id", "title", "createdAt", "updatedAt"):
        assert field in sessions[0], f"会话项缺字段 {field}（注意要给前端驼峰命名）"


def test_12_无token访问会话列表应被拒绝(client):
    r = client.get("/api/chat/sessions")
    assert r.status_code in (401, 422)


def test_13_不能访问别人的会话(client, other_token):
    """越权检查：别的账号的会话 id 不能被我读到"""
    other_sid = _new_session(client, other_token)
    r = client.get(f"/api/chat/session/{other_sid}/messages", headers=_auth(
        _login_or_register(client, "smoke_main2", "smoke123456")
    ))
    assert r.status_code == 404, f"期望 404（会话不属于我），实际 {r.status_code}"


def test_14_删除自己的会话后查不到(client, token):
    sid = _new_session(client, token)
    r = client.delete(f"/api/chat/session/{sid}", headers=_auth(token))
    assert r.status_code == 200

    r = client.get(f"/api/chat/session/{sid}/messages", headers=_auth(token))
    assert r.status_code == 404, "会话已删，却还能查到消息"


def test_15_删除不存在的会话应返回404(client, token):
    r = client.delete("/api/chat/session/999999999", headers=_auth(token))
    assert r.status_code == 404


# ======================================================================
# 五、限流
# ======================================================================
def test_16_超过限流阈值应返回429(client):
    """
    这条不需要调大模型：故意用一个不存在的会话 id，
    请求会在「限流检查」之后、真正生成之前就失败，所以很便宜。

    阈值是 cache/chat_cache.py 里的 RATELIMIT_MAX（默认 20 次/分钟）。
    为了不干扰其他用例，这里专门用一个独立账号。
    """
    token = _login_or_register(client, "smoke_ratelimit", "smoke123456")
    headers = _auth(token)

    statuses: list[int] = []
    for _ in range(25):
        r = client.post(
            "/api/chat/stream",
            json={"sessionId": 999999999, "message": "限流测试"},
            headers=headers,
        )
        statuses.append(r.status_code)
        if r.status_code == 429:
            break

    assert 429 in statuses, (
        f"连续发了 {len(statuses)} 次都没触发限流，状态码分布："
        f"{ {s: statuses.count(s) for s in set(statuses)} }\n"
        f"请检查 cache/chat_cache.py 里 check_ratelimit 是否真的被调用了"
    )
    assert 200 not in statuses, "不该有成功的请求（用的是不存在的会话 id）"


# ======================================================================
# 六、需要真实调用大模型的用例（默认跳过）
# ======================================================================
@skip_llm
def test_17_闲聊能拿到完整回复(client, token):
    sid = _new_session(client, token)
    answer = _chat(client, token, sid, "你好")
    assert answer, "回复是空的"
    assert len(answer) > 2


@skip_llm
def test_18_新闻问答有回答且不是纯拒绝(client, token):
    sid = _new_session(client, token)
    answer = _chat(client, token, sid, "今天有什么新闻？")
    assert answer, "回复是空的"


@skip_llm
def test_19_流式回复会落库(client, token):
    """SSE 推完之后，历史接口应该能查到这轮问答"""
    sid = _new_session(client, token)
    _chat(client, token, sid, "你好")
    time.sleep(1.5)                      # 落库在生成器 finally 里，给它一点时间

    msgs = client.get(f"/api/chat/session/{sid}/messages", headers=_auth(token)).json()["data"]["list"]
    roles = [m["role"] for m in msgs]
    assert "user" in roles, "用户消息没落库"
    assert "assistant" in roles, "AI 回复没落库（检查 SSE 生成器的 finally 分支）"


@skip_llm
def test_20_别人删我的会话不该删掉我的消息(client, token, other_token):
    """
    这条是冲着 crud/chat.py::delete_session 去的。

    那个函数先执行 delete(ChatMessage).where(session_id == ...) 然后 commit，
    **where 条件里没有 user_id**。所以别人拿我的 session_id 来删，
    虽然最后删会话那一步会因 user_id 不匹配而失败（返回 404），
    但我的消息已经在前面那一步被删掉并提交了。

    期望：B 删 A 的会话返回 404，且 A 的消息在数据库里仍然还在。

    ⚠️ 这里必须直接查库，不能走 HTTP 读消息接口 —— 详见 _count_messages_in_db 的说明。
    """
    sid = _new_session(client, token)
    _chat(client, token, sid, "你好")
    time.sleep(2.0)                      # 落库在生成器 finally 里，给它一点时间

    before = _count_messages_in_db(sid)
    assert before > 0, (
        f"前置条件不成立：会话 {sid} 在 PG 里一条消息都没有，"
        f"可能是落库失败或等待时间不够"
    )

    r = client.delete(f"/api/chat/session/{sid}", headers=_auth(other_token))
    assert r.status_code == 404, f"期望 404（这不是 B 的会话），实际 {r.status_code}"

    after = _count_messages_in_db(sid)
    assert after == before, (
        f"越权删除：B 删除 A 的会话后，A 的消息在数据库里从 {before} 条变成了 {after} 条。\n"
        f"根因在 crud/chat.py::delete_session —— 删 ChatMessage 的语句只有\n"
        f"    where(ChatMessage.session_id == session_id)\n"
        f"少了 user_id 条件，而它和后面删会话的语句在同一个事务里被一起 commit 了。"
    )
