from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column
from models.favorite import Base  # 沿用项目统一的 Base

class ChatSession(Base):
    """
    聊天会话表 ORM 模型
    """
    __tablename__ = "chat_session"

    # 索引：按 user_id 查会话列表是高频操作，加索引加速
    __table_args__ = (
        Index("fk_chat_session_user_idx", "user_id"),
    )

    # 会话ID，主键自增
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="会话ID")
    # 用户ID，不建跨库外键（user 表在 MySQL，chat 表在 PostgreSQL）
    # 应用层通过 JWT 保证 user_id 的合法性
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="用户ID")
    # 会话标题：格式如 "2026-9-3-15.30"，最长 50 字符够用
    title: Mapped[str] = mapped_column(String(50), nullable=False, comment="会话名称（格式：年-月-日-时.分）")
    # 创建时间：会话建立时自动写入
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    # 更新时间：每次新增消息时刷新，用于会话列表按最近活跃排序
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    def __repr__(self):
        return f"<ChatSession(id={self.id}, user_id={self.user_id}, title='{self.title}')>"


class ChatMessage(Base):
    """
    聊天消息表 ORM 模型

    每条记录代表会话中的一条消息（用户提问 或 AI 回复）。
    按 session_id + created_at 排序即为完整对话历史。
    """
    __tablename__ = "chat_message"

    # 索引：按 session_id 查消息历史是高频操作
    __table_args__ = (
        Index("fk_chat_message_session_idx", "session_id"),
    )

    # 消息ID，主键自增
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="消息ID")
    # 会话ID，外键关联 chat_session 表
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey(ChatSession.id), nullable=False, comment="会话ID")
    # 消息角色：user（用户提问）/ assistant（AI 回复）
    role: Mapped[str] = mapped_column(String(20), nullable=False, comment="消息角色：user/assistant")
    # 消息内容：用 Text 类型，支持长文本（AI 回复可能很长）
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息内容")
    # 创建时间：消息发送时间，用于历史消息按时间排序
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False, comment="创建时间")

    def __repr__(self):
        return f"<ChatMessage(id={self.id}, session_id={self.session_id}, role='{self.role}')>"
