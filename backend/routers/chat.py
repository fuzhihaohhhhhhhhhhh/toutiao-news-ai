from datetime import datetime
from functools import lru_cache
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession# 异步会话
from starlette import status
from config.pg_config import get_pg_db
from crud import chat as chat_crud
from cache import chat_cache
from models.users import User
from schemas.chat import (
    CreateSessionResponse,
    SessionItem,
    SessionListResponse,
    ChatRequest,
    HistoryListResponse,
)
from utils.auth import get_current_user
from utils.response import success_response
from utils.logger_handler import logger
from langchain_core.messages import HumanMessage, AIMessage
from ai_agent.graph import build_graph

router = APIRouter(prefix="/api/chat", tags=["chat"])


@lru_cache(None)
def get_graph():
    """全局单例：LangGraph 工作流只编译一次，避免每次请求重新构建"""
    return build_graph()


def generate_session_title() -> str:
    """
    生成会话标题：格式 年-月-日-时.分
    示例：2026-9-3-15.30
    - 月/日/时不补零（9 不是 09）
    - 分钟补两位（5 → 05）
    """
    now = datetime.now()
    return f"{now.year}-{now.month}-{now.day}-{now.hour}.{now.minute:02d}"

# 会话管理
@router.post("/session")
async def create_session(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_db),
):
    """
    新建对话
    流程：写 PostgreSQL → 清 Redis 会话列表缓存（下次查询会重新加载）
    对话名称：自动用当前时间生成（如 2026-9-3-15.30）
    """
    title = generate_session_title()
    session = await chat_crud.create_session(db, user.id, title)
    # 清缓存：下次查会话列表会从数据库重新加载
    await chat_cache.clear_sessions_cache(user.id)
    logger.info(f"[聊天] 用户{user.id}新建会话 id={session.id} title={title}")
    return success_response(
        message="新建对话成功",
        data=CreateSessionResponse(session_id=session.id, title=title),
    )


@router.get("/sessions")
async def get_session_list(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_db),
):
    """
    获取对话列表（按最近活跃倒序）
    流程：先查 Redis → 未命中查 PostgreSQL → 写回 Redis
    """
    # 1. 先查缓存
    cached = await chat_cache.get_sessions_cache(user.id)
    if cached is not None:
        return success_response(
            message="获取对话列表成功（缓存）",
            data=SessionListResponse(list=cached),
        )

    # 2. 缓存未命中，查数据库
    sessions = await chat_crud.get_session_list(db, user.id)
    items = [
        SessionItem(
            id=s.id,
            title=s.title,
            created_at=s.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            updated_at=s.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        for s in sessions
    ]

    # 3. 写回缓存
    await chat_cache.set_sessions_cache(
        user.id,
        [item.model_dump(by_alias=True) for item in items],
    )

    return success_response(
        message="获取对话列表成功",
        data=SessionListResponse(list=items),
    )


@router.delete("/session/{session_id}")
async def delete_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_db),
):
    """
    删除对话（同时删数据库 + 缓存）

    流程：删 PostgreSQL（消息+会话）→ 删 Redis（历史缓存+会话列表缓存）
    """
    # 1. 删数据库（消息 + 会话）
    ok = await chat_crud.delete_session(db, user.id, session_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或不属于当前用户",
        )

    # 2. 删缓存（历史 + 会话列表）
    await chat_cache.clear_history_cache(user.id, session_id)
    await chat_cache.clear_sessions_cache(user.id)

    logger.info(f"[聊天] 用户{user.id}删除会话 id={session_id}")
    return success_response(message="删除对话成功")


@router.get("/session/{session_id}/messages")
async def get_session_messages(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_db),
):
    """
    获取某会话的历史消息（前端切换/进入会话时调用）
    流程：先查 Redis → 未命中查 PostgreSQL → 写回 Redis
    """
    # 会话归属校验
    chat_session = await chat_crud.get_user_session(db, session_id, user.id)
    if not chat_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或不属于当前用户",
        )

    # 先查缓存
    history_data = await chat_cache.get_history_cache(user.id, session_id)
    if history_data is None:
        # 缓存未命中，查数据库并写回缓存
        db_messages = await chat_crud.get_messages(db, session_id)
        history_data = [
            {"role": m.role, "content": m.content}
            for m in db_messages
        ]
        await chat_cache.set_history_cache(user.id, session_id, history_data)

    return success_response(
        message="获取历史消息成功",
        data=HistoryListResponse(list=history_data),
    )


def _sse_event(event_type: str, **extra) -> str:
    """
    构造一条 SSE 事件
    SSE 规范：每条事件以 data: 开头、空行（\\n\\n）结尾；
    JSON 编码保证内容里的换行符不会破坏事件边界。
    """
    payload = {"type": event_type, **extra}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def chat_stream(
    data: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_db),
):
    """
    发送消息并 SSE 流式获取 AI 回复
    完整流程：
        1. 限流检查
        2. 校验会话归属（防跨用户写消息）
        3. 加载历史消息（Redis → PostgreSQL）
        4. 先存用户消息（客户端中途断开也不丢）
        5. graph.astream(stream_mode="messages") 逐 token 推送：
           - 过滤 classify 节点的分类 token，只透传正文
           - 按 id 去重由 LangGraph 保证，不会重复推送
        6. 流结束（含断连）把完整 AI 回复存库 + 更新缓存（best-effort）
    """
    # 1. 限流
    allowed = await chat_cache.check_ratelimit(user.id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="提问太频繁，请稍后再试",
        )

    # 2. 校验会话归属（同时校验存在性，防止往别人会话里塞消息）
    chat_session = await chat_crud.get_user_session(db, data.session_id, user.id)
    if not chat_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或不属于当前用户",
        )

    # 3. 加载历史（先缓存后数据库）
    history_data = await chat_cache.get_history_cache(user.id, data.session_id)
    if history_data is None:
        # 缓存未命中，查数据库
        db_messages = await chat_crud.get_messages(db, data.session_id)
        history_data = [
            {"role": m.role, "content": m.content}
            for m in db_messages
        ]
        # 写回缓存
        await chat_cache.set_history_cache(user.id, data.session_id, history_data)

    # 4. 构造 LangChain messages（历史 + 本次新消息）
    messages = []
    for m in history_data:
        if m["role"] == "user":
            messages.append(HumanMessage(content=m["content"]))
        else:
            messages.append(AIMessage(content=m["content"]))
    # 追加本次用户消息
    messages.append(HumanMessage(content=data.message))

    # 先存用户消息（流式开始前落库，断连不丢）
    await chat_crud.add_message(db, data.session_id, "user", data.message)
    # 双写：同步追加到 Redis 历史缓存（否则缓存里的历史缺用户消息，
    # 导致 AI 看不到之前用户说过的话、前端重新进入会话时用户问题不显示）
    await chat_cache.append_history_cache(user.id, data.session_id, "user", data.message)

    graph = get_graph()

    async def sse_generator():
        reply_parts: list[str] = []
        try:
            # stream_mode="messages"：逐 token 推 (消息块, 元数据)
            async for chunk, meta in graph.astream(
                {"messages": messages, "user_id": user.id},
                stream_mode="messages",
            ):
                # 过滤意图分类节点的 token（分类过程不给用户看）
                if meta.get("langgraph_node") == "classify":
                    continue
                content = chunk.content
                if not content or not isinstance(content, str):
                    continue
                reply_parts.append(content)
                yield _sse_event("content", content=content)

            yield _sse_event("done")
        except Exception as e:
            logger.error(
                f"[聊天] SSE 生成失败 user_id={user.id} session={data.session_id}: "
                f"{type(e).__name__}: {e}"
            )
            yield _sse_event("error", message="AI 回复生成失败，请稍后重试")
        finally:
            # 流结束后存 AI 回复 + 更新缓存（断连时保存已生成的部分）
            # best-effort：存库失败只记日志，不再影响响应
            full_reply = "".join(reply_parts)
            if full_reply:
                try:
                    await chat_crud.add_message(db, data.session_id, "assistant", full_reply)
                    await chat_cache.append_history_cache(user.id, data.session_id, "assistant", full_reply)
                    # 会话列表缓存失效（updated_at 变了，排序要更新）
                    await chat_cache.clear_sessions_cache(user.id)
                except Exception as e:
                    logger.error(
                        f"[聊天] AI 回复存库失败 session={data.session_id}: "
                        f"{type(e).__name__}: {e}"
                    )

    logger.info(f"[聊天] 用户{user.id}会话{data.session_id} 流式提问 长度={len(data.message)}")
    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",   # 禁止中间层缓存流
            "Connection": "keep-alive",    # 保持长连接
            "X-Accel-Buffering": "no",     # nginx 代理时不缓冲，token 立即到达前端
        },
    )
