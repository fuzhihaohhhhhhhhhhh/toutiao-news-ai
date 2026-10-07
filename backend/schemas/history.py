# 添加历史记录请求
from datetime import datetime

from pydantic import Field, BaseModel, ConfigDict

from schemas.base import NewsItemBase


class HistoryAddRequest(BaseModel):
    news_id: int = Field(...,alias="newsId")

class HistoryNewsItemResponse(NewsItemBase):
    """
    浏览历史记录新闻项响应模型
    """
    history_id: int = Field(...,alias="historyId")
    view_time: datetime = Field(...,alias="viewTime")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )

class HistoryNewsListResponse(BaseModel):
    """
    浏览历史记录新闻列表响应模型
    """
    list: list[HistoryNewsItemResponse]
    total:int
    has_more: bool = Field(alias="hasMore")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )

