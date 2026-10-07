from fastapi import FastAPI,APIRouter,HTTPException
from fastapi.params import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from config.db_config import get_db
from crud import users
from models.users import User
from schemas.users import UserRegister, UserAuthResponse, UserInfoResponse, UserUpdateRequest, UserChangePasswordRequest
from utils.auth import get_current_user
from utils.response import success_response

#创建 APIRouter 实例 prefix 路径前缀 tags 标签分类
router = APIRouter(prefix="/api/user",tags=["users"])

@router.post("/register")
async def register_user(user_data: UserRegister,db: AsyncSession = Depends(get_db),):

  # 注册逻辑：检查用户名是否存在->不存在创建用户->生成token->响应结果
  existing_user = await users.get_user_by_username(db,user_data.username)
  if existing_user:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="用户名已存在")

  # 创建用户
  user = await users.create_user(db,user_data)
  # 生成token
  token = await users.create_token(db,user.id)

  # return {
  #   "code": 200,
  #   "message": "注册成功",
  #   "data": {
  #     "token": token,
  #     "userInfo": {
  #       "id": user.id,
  #       "username": user.username,
  #       "bio": user.bio,
  #       "avatar": user.avatar
  #   }
  # }
  response_data = UserAuthResponse(token=token,user_info=UserInfoResponse.model_validate(user))
  return success_response(message="注册成功",data=response_data)

@router.post("/login")
async def login_user(user_data: UserRegister,db: AsyncSession = Depends(get_db)):
  # 登录逻辑：检查用户名是否存在->密码是否正确->生成token->响应结果
  user = await users.authenticate_user(db,user_data.username,user_data.password)
  if not user:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="用户名或密码错误")
  token = await users.create_token(db,user.id)
  response_data = UserAuthResponse(token=token,user_info=UserInfoResponse.model_validate(user))
  return success_response(message="登录成功",data=response_data)

@router.get("/info")
async def get_user_info(user: User = Depends(get_current_user)):# 依赖注入获取当前用户信息
  return success_response(message="获取用户信息成功",data=UserInfoResponse.model_validate(user))

# 更新用户信息: 验证token->更新用户信息->请求体参数->定义pydantic模型->响应结果
# 参数：用户输入的，验证token的，db(调用更新操作)
@router.put("/update")
async def update_user_info(
        user_data: UserUpdateRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
  # 更新用户信息
  update_user = await users.update_user_info(db,user.username,user_data)
  return success_response(message="更新用户信息成功",data=UserInfoResponse.model_validate(update_user))

# 修改密码
@router.put("/password")
async def change_password(
        password_data: UserChangePasswordRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
  # 修改密码
  res_change_pwd = await users.change_password(db,user,password_data)
  if not res_change_pwd:
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,detail="密码修改失败,请稍后再试")
  return success_response(message="密码修改成功")
