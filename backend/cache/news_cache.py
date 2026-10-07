from typing import List, Dict, Any, Optional

from config.cache_config import get_json_cache, set_cache

# 新闻相关的缓存模块
# key - value
CATEGORIES_KEY = "news:categories"
NEWS_LIST_PREFIX = "news_list:"
NEWS_DETAIL_PREFIX = "news:detail:"
RELATED_NEWS_PREFIX = "news:related:"

# 获取新闻分类的缓存方法
async def get_news_categories_cache():
    return await get_json_cache(CATEGORIES_KEY)

# 写入新闻分类缓存：缓存的数据，过期时间
# 分类、配置 7200秒过期，列表：600秒过期， 详情：1800秒过期， 验证码：120秒过期  数据越稳定，过期时间越长
async def set_news_categories_cache(data: List[Dict[str,Any]],expire: int = 7200):
    await set_cache(CATEGORIES_KEY,data,expire)

# 写入缓存-新闻列表
async def set_cache_news_list(
        category_id:Optional[int],
        page: int,
        size:int,
        news_list: List[Dict[str,Any]],
        expire: int = 600,
):
    # 调用 封装的 Redis 的设置方法，存新闻列表到缓存
    category_part = category_id if category_id is not None else "all"
    key = f"{NEWS_LIST_PREFIX}{category_part}:{page}:{size}"
    return await set_cache(key,news_list,expire)

# 读取缓存-新闻列表
async def get_cache_news_list(
        category_id:Optional[int],
        page: int,
        size:int,
):
    # 调用 封装的 Redis 的读取方法，从新闻列表缓存中读取数据
    category_part = category_id if category_id is not None else "all"
    key = f"{NEWS_LIST_PREFIX}{category_part}:{page}:{size}"
    return await get_json_cache(key)


# 写入缓存-新闻详情
async def set_cache_news_detail(
        news_id: int,
        news_data: Dict[str, Any],
        expire: int = 1800,
):
    key = f"{NEWS_DETAIL_PREFIX}{news_id}"
    return await set_cache(key, news_data, expire)


# 读取缓存-新闻详情
async def get_cache_news_detail(news_id: int):
    key = f"{NEWS_DETAIL_PREFIX}{news_id}"
    return await get_json_cache(key)


# 写入缓存-相关新闻
async def set_cache_related_news(
        news_id: int,
        limit: int,
        related_news: List[Dict[str, Any]],
        expire: int = 600,
):
    key = f"{RELATED_NEWS_PREFIX}{news_id}:{limit}"
    return await set_cache(key, related_news, expire)


# 读取缓存-相关新闻
async def get_cache_related_news(
        news_id: int,
        limit: int,
):
    key = f"{RELATED_NEWS_PREFIX}{news_id}:{limit}"
    return await get_json_cache(key)