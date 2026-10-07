import hashlib
import json
import redis as sync_redis
from langchain_core.embeddings import Embeddings
from config.cache_config import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD
from models.factory import get_embed_model

# 缓存 key 前缀
EMBEDDING_CACHE_PREFIX = "embedding:"

# 7 天能覆盖一周内的热点查询；过期后自动重新计算，不影响功能
EMBEDDING_CACHE_TTL = 7 * 24 * 3600

sync_redis = sync_redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    # 空串→None：redis-py 收到 "" 会真的发一次 AUTH ""，会让无密码实例连接失败
    password=REDIS_PASSWORD or None,
    decode_responses=True,        # 自动把 bytes 解码成 str，方便 json 处理
    socket_connect_timeout=3,     # 连接超时 3 秒，连不上不卡住主流程
    socket_timeout=3,             # 读写超时 3 秒
    protocol=2,                   # 使用 RESP2 协议，兼容老版本 Redis（与 cache_config.py 保持一致，避免 HELLO 命令报错）
)


def make_cache_key(text: str) -> str:
    """
    根据文本内容生成缓存 key
    """
    text_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"{EMBEDDING_CACHE_PREFIX}{text_hash}"


class CachedEmbeddings(Embeddings):
    """
    带 Redis 缓存的 Embeddings 包装类
    """

    def __init__(self, underlying: Embeddings):
        #underlying: Embeddings 实例, 用于计算向量
        self.underlying = underlying

    def embed_query(self, text: str) -> list[float]:
        """
        把一段文本转向量（查询场景）
        """
        key = make_cache_key(text)

        # ── 第 1 步：查缓存 ──
        try:
            cached = sync_redis.get(key)
            if cached:
                # 命中缓存：json 字符串 → list[float]，直接返回，不调底层
                return json.loads(cached)
        except Exception:
            # Redis 查询失败（如 Redis 挂了）→ 静默降级，继续走底层
            pass

        # ── 第 2 步：未命中，调底层 Embeddings 计算向量（会调 DashScope）──
        vector = self.underlying.embed_query(text)

        # ── 第 3 步：把算好的向量存入 Redis，供下次复用 ──
        try:
            sync_redis.set(key, json.dumps(vector), ex=EMBEDDING_CACHE_TTL)
        except Exception:
            # 写入失败（如 Redis 满了）→ 静默降级，不影响返回
            pass

        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """
        把多段文本批量转向量（索引场景）
        """
        return [self.embed_query(t) for t in texts]


def get_cached_embed_model() -> CachedEmbeddings:
    """
    获取带 Redis 缓存的 Embeddings 实例
    """
    return CachedEmbeddings(get_embed_model())
