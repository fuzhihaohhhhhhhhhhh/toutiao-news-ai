from contextlib import asynccontextmanager

from fastapi import FastAPI
from routers import news, users, favorite, history, chat
from fastapi.middleware.cors import CORSMiddleware

from config.cache_config import check_redis_connection
from utils.exception_handlers import register_exception_handlers


@asynccontextmanager# 让你可以在 FastAPI 的启动阶段做初始化（连数据库、连 Redis），在关闭阶段做清理，整个应用生命周期就被这个生成器函数包住了。
async def lifespan(app: FastAPI):
    redis_ok = await check_redis_connection()
    if not redis_ok:
        print("[WARN] Redis 未连接，缓存功能不可用，请先启动 redis-server.exe redis.windows.conf")
    else:
        print("[INFO] 缓存服务就绪")
    yield


app = FastAPI(lifespan=lifespan)

# 注册异常处理函数
register_exception_handlers(app)


#解决跨域问题（前后端请求的协议、域名、端口不一致时设置）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], #允许访问的来源
    allow_credentials=True, #是否允许携带Cookie
    allow_methods=["*"], #允许的请求方法
    allow_headers=["*"], #允许的请求头
)

@app.get("/")
async def root():
    return {"message": "Hello World"}


# 挂载路由
app.include_router(news.router)
app.include_router(users.router)
app.include_router(favorite.router)
app.include_router(history.router)
app.include_router(chat.router)