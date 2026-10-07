from datetime import datetime
from typing import Optional

from pydantic import Field, ConfigDict, BaseModel

# 新闻模型类
class NewsItemBase(BaseModel):
    id:int
    title:str
    description:Optional[str] = None
    image:Optional[str] = None
    author:Optional[str] = None
    category_id: int = Field(alias="categoryId")# Field 用来给模型字段“附加配置和约束”，是类型注解之外的补充。
    views:int
    publish_time:Optional[datetime] = Field(None,alias="publishedTime")

    model_config = ConfigDict(
        populate_by_name=True,# 允许用"字段原名"或"alias"给模型赋值
        from_attributes=True,# 允许从模型实例中直接赋值
    )
