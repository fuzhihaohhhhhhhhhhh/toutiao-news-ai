from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from config.db_config import get_db
from crud import history
from models.users import User
from schemas.history import HistoryAddRequest, HistoryNewsListResponse
from utils.auth import get_current_user
from utils.response import success_response

router = APIRouter(prefix="/api/history",tags=["history"])

#添加历史记录
@router.post("/add")
async def add_history(
    data: HistoryAddRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    添加历史记录
    """
    result = await history.add_history(db,user.id,data.news_id)
    return success_response(message="添加历史记录成功",data=result)

#获取浏览历史记录列表
@router.get("/list")
async def get_history_list(
        page: int = Query(1,description="页码",ge=1),
        page_size: int = Query(10,alias="pageSize",description="每页数量",ge=1,le=100),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    rows,total = await history.get_history_list(db,user.id,page,page_size)
    history_list = [{
        **news.__dict__,
        "view_time": view_time,
        "history_id": history_id,
    } for news, view_time,history_id in rows]
    has_more = total > page*page_size
    data = HistoryNewsListResponse(
        list=history_list,
        total=total,
        hasMore=has_more,
    )
    return success_response(message="获取浏览历史记录列表成功",data=data)

# 删除单条浏览历史记录
@router.delete("/delete/{history_id}")
async def delete_history(
    history_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    删除单条浏览历史记录
    """
    result = await history.delete_history(db,user.id,history_id)
    if not result:
        from starlette.exceptions import HTTPException
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="历史记录不存在")
    else:
        return success_response(message="删除历史记录成功")


@router.delete("/clear")
async def delete_history_list(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    删除用户浏览历史记录列表
    """
    result = await history.remove_history_list(db,user.id)
    return success_response(message="删除历史记录成功",data=result)
