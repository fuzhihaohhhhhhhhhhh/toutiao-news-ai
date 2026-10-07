from datetime import datetime
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from models.chat import ChatSession, ChatMessage
async def create_session(db: AsyncSession, user_id: int, title: str) -> ChatSession:
    """
    新建会话
    :param db
    :param user_id
    :param title
    :return
    """
    session = ChatSession(user_id=user_id, title=title)
    db.add(session)
    await db.commit()
    await db.refresh(session)  # 刷新拿到数据库分配的 id
    return session


async def get_session_list(db: AsyncSession, user_id: int) -> list[ChatSession]:
    """
    获取用户的会话列表（按最近活跃倒序）
    :param db
    :param user_id
    :return
    """
    query = (
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_user_session(db: AsyncSession, session_id: int, user_id: int) -> ChatSession | None:
    """
    查询会话（带用户归属校验）
    :param db
    :param session_id
    :param user_id
    :return
    """
    query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == user_id,
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def delete_session(db: AsyncSession, user_id: int, session_id: int) -> bool:
    """
    删除会话（同时删该会话的所有消息）
    删除顺序：先删消息（子表），再删会话（父表），避免外键约束冲突。

    :param db
    :param user_id
    :param session_id
    :return
    """
    # 1. 先确认这个会话确实属于当前用户；不属于就直接返回 False（路由层会转成 404）
    if not await get_user_session(db, session_id, user_id):
        return False

    # 2. 归属确认了，再删该会话的所有消息
    await db.execute(
        delete(ChatMessage).where(ChatMessage.session_id == session_id)
    )
    # 3. 再删会话本身（仍然带 user_id 条件，做双保险）
    result = await db.execute(
        delete(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        )
    )
    await db.commit()
    # rowcount > 0 表示确实删了一行
    return result.rowcount > 0


async def add_message(db: AsyncSession, session_id: int, role: str, content: str) -> ChatMessage:
    """
    添加一条消息，并更新会话的 updated_at
    :param db
    :param session_id
    :param role
    :param content
    :return
    """
    message = ChatMessage(session_id=session_id, role=role, content=content)
    db.add(message)
    # 更新会话的 updated_at，让会话列表按最近活跃排序
    await db.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(updated_at=datetime.now())
    )
    await db.commit()
    await db.refresh(message)
    return message


async def get_messages(db: AsyncSession, session_id: int) -> list[ChatMessage]:
    """
    获取会话的所有消息（按时间升序，保证对话顺序）
    :param db
    :param session_id
    :return
    """
    query = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    result = await db.execute(query)
    return list(result.scalars().all())
