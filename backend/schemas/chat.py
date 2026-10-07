from datetime import datetime

from pydantic import Field, BaseModel, ConfigDict


class CreateSessionResponse(BaseModel):
    """新建对话的响应"""
    session_id: int = Field(..., alias="sessionId")
    title: str

    model_config = ConfigDict(populate_by_name=True)


class SessionItem(BaseModel):
    """会话列表中的一项"""
    id: int
    title: str
    created_at: str = Field(..., alias="createdAt")
    updated_at: str = Field(..., alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class SessionListResponse(BaseModel):
    """会话列表响应"""
    list: list[SessionItem]

    model_config = ConfigDict(populate_by_name=True)


class ChatRequest(BaseModel):
    """发消息请求"""
    session_id: int = Field(..., alias="sessionId")
    message: str

    model_config = ConfigDict(populate_by_name=True)


class ChatResponse(BaseModel):
    """发消息响应（非流式，先跑通用；后续改 SSE 流式）"""
    reply: str

    model_config = ConfigDict(populate_by_name=True)


class MessageItem(BaseModel):
    """历史消息项"""
    role: str
    content: str


class HistoryListResponse(BaseModel):
    """历史消息列表响应（前端切换会话时加载）"""
    list: list[MessageItem]
