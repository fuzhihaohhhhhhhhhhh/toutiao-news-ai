from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from models.news import Category, News


async def get_categories(db: AsyncSession,skip: int = 0, limit: int = 100):
    stmt = select(Category).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_news_list(db: AsyncSession,category_id: int,skip: int = 0,limit: int = 10):
    # 查询指定分类下的新闻数据
    stmt = select(News).where(News.category_id == category_id).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_news_total(db: AsyncSession,category_id: int):
    # 查询指定分类下的新闻总数
    stmt = select(func.count(News.id)).where(News.category_id == category_id)
    result = await db.execute(stmt)
    return result.scalar()

async def get_news_detail(db: AsyncSession,news_id: int):
    # 查询指定新闻的详情
    stmt = select(News).where(News.id == news_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

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

async def get_related_news(db: AsyncSession,news_id: int,category_id: int,limit: int = 5):
    # 查询指定新闻的相关新闻 order_by:推荐新闻排序，按浏览量和发布时间降序排序
    stmt = (select(News)
            .where(News.category_id == category_id,News.id != news_id)
            .order_by(News.views.desc(),News.publish_time.desc())
            .limit(limit))
    result = await db.execute(stmt)
    # return result.scalars().all()
    related_news = result.scalars().all()
    # 列表推导式 推到出相关新闻的核心数据再return
    return  [{
        "id":news.id,
        "title":news.title,
        "content":news.content,
        "image":news.image,
        "author":news.author,
        "publishTime":news.publish_time,
        "categoryId":news.category_id,
        "views":news.views,
    }  for news in related_news]



