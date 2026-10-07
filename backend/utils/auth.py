from fastapi import Depends,Header,HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from crud import users
from config.db_config import get_db
from starlette import status

# 整合，根据token查询用户信息，返回用户数据
async def get_current_user(
        authorization: str = Header(...,alias="Authorization"),
        db: AsyncSession = Depends(get_db)
):
    token = authorization.replace("Bearer ","")
    user = await users.get_user_by_token(db,token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="无效的token或token过期")
    return user
