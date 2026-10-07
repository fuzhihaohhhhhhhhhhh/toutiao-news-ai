from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.favorite import Favorite
from models.news import News


async def is_news_favorited(
    db: AsyncSession,
    user_id: int,
    news_id: int,
) -> bool:
    """
    检查用户是否收藏了新闻
    """
    query = select(Favorite).where(Favorite.user_id == user_id,Favorite.news_id == news_id)
    result = await db.execute(query)
    # 检查是否有收藏记录存在
    return result.scalar_one_or_none() is not None

# 添加收藏
async def add_favorited(
        db: AsyncSession,
        user_id: int,
        news_id: int,
) -> None:
    """
    添加用户收藏记录
    """
    favorite = Favorite(user_id=user_id,news_id=news_id)
    db.add(favorite)
    await db.commit()
    await db.refresh(favorite)
    return favorite

# 取消收藏
async def remove_favorited(
        db: AsyncSession,
        user_id: int,
        news_id: int,
) -> None:
    """
    取消收藏新闻
    """
    query = delete(Favorite).where(Favorite.user_id == user_id,Favorite.news_id == news_id)
    result = await db.execute(query)
    await db.commit()
    return result.rowcount>0

# 获取收藏列表
async def get_favorite_list(
        db: AsyncSession,
        user_id: int,
        page: int = 1,
        page_size: int = 10,
) -> list:
    """
    获取用户收藏列表
    """
    # 总量 + 收藏的新闻列表
    count_query = select(func.count()).where(Favorite.user_id == user_id)
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    # 获取收藏列表 - 联表查询 join（） + 收藏时间排序 + 分页
    # Favorite.created_at.label("favorite_time") 起别名
    query = (select(News,Favorite.created_at.label("favorite_time"),Favorite.id.label("favorite_id"))
     .join(Favorite,Favorite.news_id == News.id)
     .where(Favorite.user_id == user_id)
     .order_by(Favorite.created_at.desc())
     .offset((page-1)*page_size)
     .limit(page_size))
    result = await db.execute(query)
    rows = result.all()
    return rows,total

# 清空收藏列表
async def remove_favorite_list(
    db: AsyncSession,
    user_id: int,
):
    """
    清空用户收藏列表
    """
    query = delete(Favorite).where(Favorite.user_id == user_id)
    result = await db.execute(query)
    await db.commit()

    # 返回一个删除的数量
    return result.rowcount or 0



