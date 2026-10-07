import uuid
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import select,update,delete
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from models.users import User, UserToken
from schemas.users import UserRegister, UserUpdateRequest, UserChangePasswordRequest
from utils import security


# 根据用户名，查询数据库
async def get_user_by_username(db: AsyncSession,username: str):
    query = select(User).where(User.username == username)
    result = await db.execute(query)
    return result.scalars().one_or_none()


# 创建用户
async def create_user(db: AsyncSession,user_data: UserRegister):
    # 密码加密
    hashed_password = security.get_hash_password(user_data.password)
    user = User(username=user_data.username,password=hashed_password)
    db.add(user)
    await db.commit()
    await db.refresh(user) # 从数据库读回最新的user
    return user

# 生成token

async def create_token(db: AsyncSession, user_id: int):
    # 生成token + 过期时间-> 查询数据库中用户是否存在token 有：更新token 无：生成新token
    token = str(uuid.uuid4())
    # 过期时间 7天
    expires_at = datetime.now()+timedelta(days=7)
    #查询数据库中用户是否存在token
    query = select(UserToken).where(UserToken.user_id == user_id)
    result = await db.execute(query)
    user_token = result.scalars().one_or_none()

    if user_token:
        # 更新token
        user_token.token = token
        user_token.expires_at = expires_at
    else:
        # 生成新token
        user_token = UserToken(user_id=user_id,token=token,expires_at=expires_at)
        db.add(user_token)

    await db.commit()

    return token

async def authenticate_user(db: AsyncSession,username: str,password: str):
    # 查询数据库中用户是否存在
    user = await get_user_by_username(db,username)
    if not user:
        return None
    # 校验密码是否正确
    if not security.verify_password(password,user.password):
        return None
    return user

async def get_user_by_token(db: AsyncSession,token: str):
    query = select(UserToken).where(UserToken.token == token)
    result = await db.execute(query)
    db_token = result.scalars().one_or_none()
    if not db_token or db_token.expires_at < datetime.now():
        return None

    query = select(User).where(User.id == db_token.user_id)
    result = await db.execute(query)
    return result.scalars().one_or_none()

# 更新用户信息-> 校验用户是否存在-> 更新用户信息-> 返回更新后的用户信息
async def update_user_info(db: AsyncSession,username: str,user_data: UserUpdateRequest):
    # user_data 是一个pydantic模型，需要转换为字典格式，然后解包
    # 没有设置值的字段，不会被更新
    query = update(User).where(User.username == username).values(**user_data.model_dump(
        exclude_unset=True,# 排除未设置值的字段
        exclude_none=True,# 排除None值
    ))
    result = await db.execute(query)
    await db.commit()

    # 检查更新
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="用户不存在")

    # 获取更新后的用户信息
    update_user = await get_user_by_username(db,username)
    return update_user

# 修改密码: 校验旧密码是否正确-> 新密码加密-> 更新数据库密码-> 返回更新后的用户信息
async def change_password(db: AsyncSession,user: User,password_data: UserChangePasswordRequest):
    # 校验旧密码是否正确
    if not security.verify_password(password_data.old_password,user.password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="旧密码错误")

    #新密码加密
    hashed_password = security.get_hash_password(password_data.new_password)
    # 更新数据库密码
    user.password = hashed_password
    # 更新：由SQLAlchemy真正接管这个User对象，确保可以commit
    # 规避：session 过期或者关闭导致不能提交的问题
    db.add(user)
    await db.commit()
    # 获取更新后的用户信息
    await db.refresh(user)
    return True






