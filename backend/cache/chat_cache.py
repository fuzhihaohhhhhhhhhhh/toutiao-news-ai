import json
from typing import Optional

from config.cache_config import redis_client, get_json_cache, set_cache


SESSIONS_KEY_PREFIX = "chat:sessions:"
HISTORY_KEY_PREFIX = "chat:history:"
RATELIMIT_KEY_PREFIX = "chat:ratelimit:"

HISTORY_TTL = 7200       # 历史消息缓存 2 小时
SESSIONS_TTL = 1800      # 会话列表缓存 30 分钟
RATELIMIT_MAX = 20        # 每用户每分钟最多 20 次提问
RATELIMIT_WINDOW = 60     # 限流窗口 60 秒


async def get_sessions_cache(user_id: int) -> Optional[list]:
    """读取会话列表缓存，未命中返回 None"""
    key = f"{SESSIONS_KEY_PREFIX}{user_id}"
    return await get_json_cache(key)


async def set_sessions_cache(user_id: int, sessions: list):
    """写入会话列表缓存"""
    key = f"{SESSIONS_KEY_PREFIX}{user_id}"
    await set_cache(key, sessions, SESSIONS_TTL)

async def clear_sessions_cache(user_id: int):
    """清除会话列表缓存（新建/删除会话后调用，下次查询会重新加载）"""
    key = f"{SESSIONS_KEY_PREFIX}{user_id}"
    await redis_client.delete(key)


async def get_history_cache(user_id: int, session_id: int) -> Optional[list]:
    """读取历史消息缓存，未命中返回 None"""
    key = f"{HISTORY_KEY_PREFIX}{user_id}:{session_id}"
    return await get_json_cache(key)


async def set_history_cache(user_id: int, session_id: int, messages: list):
    """写入历史消息缓存"""
    key = f"{HISTORY_KEY_PREFIX}{user_id}:{session_id}"
    await set_cache(key, messages, HISTORY_TTL)


async def append_history_cache(user_id: int, session_id: int, role: str, content: str):
    """
    向历史缓存追加一条新消息（不重写整个列表，省网络开销）

    :param user_id: 用户ID
    :param session_id: 会话ID
    :param role: 消息角色（user/assistant）
    :param content: 消息内容
    """
    key = f"{HISTORY_KEY_PREFIX}{user_id}:{session_id}"
    existing = await get_json_cache(key)
    if existing is None:
        # 缓存已过期或不存在，不重建（下次查询会从数据库重新加载）
        return
    existing.append({"role": role, "content": content})
    await set_cache(key, existing, HISTORY_TTL)


async def clear_history_cache(user_id: int, session_id: int):
    """清除某会话的历史缓存（删除会话时调用）"""
    key = f"{HISTORY_KEY_PREFIX}{user_id}:{session_id}"
    await redis_client.delete(key)


# ==================== 限流防刷 ====================

async def check_ratelimit(user_id: int) -> bool:
    """
    检查用户是否超过提问频率限制

    实现原理：每次调用 INCR 计数 +1，第一次设置 60 秒过期。
    计数超过 RATELIMIT_MAX 返回 False（被限流）。

    :param user_id: 用户ID
    :return: True 允许提问，False 已超限流阈值
    """
    key = f"{RATELIMIT_KEY_PREFIX}{user_id}"
    count = await redis_client.incr(key)
    if count == 1:
        # 第一次调用，设置过期时间
        await redis_client.expire(key, RATELIMIT_WINDOW)
    return count <= RATELIMIT_MAX
