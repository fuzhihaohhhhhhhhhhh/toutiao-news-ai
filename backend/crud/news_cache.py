from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from models.news import Category, News
from cache.news_cache import get_news_categories_cache, set_news_categories_cache, get_cache_news_list, \
    set_cache_news_list, get_cache_news_detail, set_cache_news_detail, \
    get_cache_related_news, set_cache_related_news
from schemas.base import NewsItemBase

# 获取新闻分类
async def get_categories(db: AsyncSession,skip: int = 0, limit: int = 100):
    # 先尝试从缓存中获取新闻分类
    cache_categories = await get_news_categories_cache()
    if cache_categories:
        return cache_categories

    # 如果缓存中没有，再从数据库中查询
    stmt = select(Category).offset(skip).limit(limit)
    result = await db.execute(stmt)
    categories = result.scalars().all()

    # 写入缓存
    if categories:
        categories = jsonable_encoder(categories)
        await set_news_categories_cache(categories)

    # 返回分类数据
    return categories



async def get_news_list(db: AsyncSession,category_id: int,skip: int = 0,limit: int = 10):
    # 先尝试从缓存中获取新闻列表
    page = skip//limit + 1
    cache_news_list = await get_cache_news_list(category_id,page,limit)
    if cache_news_list:
        return [News(**item) for item in cache_news_list]

    # 查询指定分类下的新闻数据
    stmt = select(News).where(News.category_id == category_id).offset(skip).limit(limit)
    result = await db.execute(stmt)
    news_list = result.scalars().all()

    # 写入缓存
    if news_list:
        #先把 ORM 类型转化为 Pydantic 类型，再转为 字典
        # by_alias=False 表示不使用别名,保存python格式，因为redis数据是给后端用的
        news_data = [NewsItemBase.model_validate(item).model_dump(mode="json",by_alias=False) for item in news_list]
        await set_cache_news_list(category_id,page,limit,news_data)
    return news_list

async def get_news_total(db: AsyncSession,category_id: int):
    # 查询指定分类下的新闻总数
    stmt = select(func.count(News.id)).where(News.category_id == category_id)
    result = await db.execute(stmt)
    return result.scalar()

async def get_news_detail(db: AsyncSession, news_id: int):
    # 先尝试从缓存中获取新闻详情
    cache_detail = await get_cache_news_detail(news_id)
    if cache_detail:
        return News(**cache_detail)

    # 如果缓存中没有，再从数据库中查询
    stmt = select(News).where(News.id == news_id)
    result = await db.execute(stmt)
    news_detail = result.scalar_one_or_none()

    # 写入缓存
    if news_detail:
        news_data = jsonable_encoder(news_detail)
        await set_cache_news_detail(news_id, news_data)

    return news_detail

async def update_news_views(db: AsyncSession,news_id: int):
    # 更新指定新闻的浏览量
    stmt = update(News).where(News.id == news_id).values(views=News.views + 1)
    result = await db.execute(stmt)
    await db.commit()

    # 更新->检查数据库是否真的命中了指定的新闻
    if result.rowcount > 0:
        return True
    else:
        return False

async def get_related_news(db: AsyncSession, news_id: int, category_id: int, limit: int = 5):
    # 先尝试从缓存中获取相关新闻
    cache_related = await get_cache_related_news(news_id, limit)
    if cache_related:
        return cache_related

    # 如果缓存中没有，再从数据库中查询
    stmt = (select(News)
            .where(News.category_id == category_id, News.id != news_id)
            .order_by(News.views.desc(), News.publish_time.desc())
            .limit(limit))
    result = await db.execute(stmt)
    related_news = result.scalars().all()
    news_list = [{
        "id": news.id,
        "title": news.title,
        "content": news.content,
        "image": news.image,
        "author": news.author,
        "publishTime": news.publish_time,
        "categoryId": news.category_id,
        "views": news.views,
    } for news in related_news]

    # 写入缓存
    if news_list:
        await set_cache_related_news(news_id, limit, news_list)

    return news_list