import json
from typing import Any

import redis.asyncio as redis

from config.env import env, env_int

# 连接参数从环境变量读取；密码等真实值只存在于本地 .env（不入库）
REDIS_HOST = env("REDIS_HOST", "localhost")
REDIS_PORT = env_int("REDIS_PORT", 6379)
REDIS_DB = env_int("REDIS_DB", 0)
REDIS_PASSWORD = env("REDIS_PASSWORD", "")

# 创建redis的连接对象
# protocol=2: 使用 RESP2 协议
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    # 空串必须转成 None：redis-py 收到 "" 会真的发一次 AUTH ""，
    # 会让"没有设密码"的 Redis 实例连接失败
    password=REDIS_PASSWORD or None,
    decode_responses=True,#自动解码响应
    socket_connect_timeout=3,#连接超时时间
    socket_timeout=3,#读取超时时间
    protocol=2,#使用RESP2协议
)


async def check_redis_connection() -> bool:
    """主动 Ping Redis，返回是否连通。用于应用启动时健康检查。"""
    try:
        result = await redis_client.ping()
        if result:
            print(f"[Redis] 连接成功 {REDIS_HOST}:{REDIS_PORT} (db={REDIS_DB})")
            return True
        print("[Redis] ping 返回 False")
        return False
    except Exception as e:
        print(f"[Redis] 连接失败: {e}")
        return False


#读取：字符串
async def get_cache(key: str):
    try:
        return await redis_client.get(key)
    except Exception as e:
        print(f"[Redis] 获取缓存失败 key={key}: {type(e).__name__}: {e}")
        return None


#读取：列表或者字典
async def get_json_cache(key: str):
    try:
        data = await redis_client.get(key)
        if data:
            return json.loads(data)
        else:
            return None
    except Exception as e:
        print(f"[Redis] 获取JSON缓存失败 key={key}: {type(e).__name__}: {e}")
        return None


#设置缓存  expire:过期时间，默认3600秒
async def set_cache(key: str, value: Any, expire: int = 3600):
    try:
        if isinstance(value, (dict, list)):#isinstance()函数用于判断一个对象是否是某个类的实例
            value = json.dumps(value, ensure_ascii=False)
        await redis_client.set(key, value, ex=expire)
        return True
    except Exception as e:
        print(f"[Redis] 设置缓存失败 key={key}: {type(e).__name__}: {e}")
        return False