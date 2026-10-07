from models.news import News
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# 获取所有新闻（分页加载，避免一次性全表加载到内存）
async def get_all_news(db: AsyncSession, offset: int = 0, limit: int = 500) -> list[News]:
    query = select(News).offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())
