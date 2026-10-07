from datetime import datetime

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.history import History
from models.news import News


async def add_history(
        db: AsyncSession,
        user_id: int,
        news_id: int,
):
    """
    添加用户浏览历史记录
    :param db:
    :param user_id:
    :param news_id:
    :return:
    """
    query = select(History).where(History.user_id == user_id, History.news_id == news_id)
    result = await db.execute(query)
    existing_history = result.scalar_one_or_none()
    if existing_history:
        existing_history.view_time = datetime.now()
        await db.commit()
        await db.refresh(existing_history)
        return existing_history
    else:
        history = History(
            user_id=user_id,
            news_id=news_id,
            view_time=datetime.now(),
        )
        db.add(history)
        await db.commit()
        await db.refresh(history)
        return history

# 获取浏览历史记录列表
async def get_history_list(
    db: AsyncSession,
    user_id: int,
    page: int = 1,
    page_size: int = 10,
):
    """
    获取用户浏览历史记录列表
    :param db:
    :param user_id
    :param page
    :param page_size
    :return:
    """
    count_query = select(func.count()).where(History.user_id == user_id)
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    # 获取浏览历史记录列表 - 联表查询 join（） + 浏览时间排序 + 分页
    # History.view_time.label("view_time") 起别名
    query = (select(News, History.view_time.label("view_time"), History.id.label("history_id"))
             .join(History, History.news_id == News.id)
             .where(History.user_id == user_id)
             .order_by(History.view_time.desc())
             .offset((page - 1) * page_size)
             .limit(page_size))
    result = await db.execute(query)
    rows = result.all()
    return rows, total

async def delete_history(
    db: AsyncSession,
    user_id: int,
    news_id: int,
):
    """
    删除用户浏览历史记录
    :param db:
    :param user_id
    :param news_id
    :return:
    """
    query = delete(History).where(History.user_id == user_id, History.news_id == news_id)
    result = await db.execute(query)
    await db.commit()
    return result.rowcount > 0

async def remove_history_list(
    db: AsyncSession,
    user_id: int,
):
    """
    清空用户浏览历史记录列表
    :param db:
    :param user_id
    :return:
    """
    query = delete(History).where(History.user_id == user_id)
    result = await db.execute(query)
    await db.commit()

    # 返回一个删除的数量
    return result.rowcount or 0

