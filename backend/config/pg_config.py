from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config.env import env, env_int

# ── 连接参数全部来自环境变量 ──
# 真实凭据只写在本地 .env（不入库），模板见项目根目录 .env.example
PG_HOST = env("PG_HOST", "localhost")
PG_PORT = env_int("PG_PORT", 5433)
PG_USER = env("PG_USER", "chatuser")
PG_PASSWORD = env("PG_PASSWORD", "")
PG_DB = env("PG_DB", "chat_db")

# PostgreSQL 异步连接串
# 变量名保持 PG_DATABASE_URL 不变：text/test_smoke.py 会 import 它做直连查库校验
PG_DATABASE_URL = (
    f"postgresql+asyncpg://{PG_USER}:{quote_plus(PG_PASSWORD)}"
    f"@{PG_HOST}:{PG_PORT}/{PG_DB}"
)

# 创建异步数据库引擎
pg_engine = create_async_engine(
    PG_DATABASE_URL,
    echo=False,  # 生产环境关闭 SQL 日志（调试时可改 True）
    pool_size=5,  # 连接池大小（聊天场景不需要太大）
    max_overflow=10,  # 允许的额外连接数
)

# 创建异步会话工厂
PgSessionLocal = async_sessionmaker(
    bind=pg_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 提交后不重新查询，和 db_config 保持一致
)

# 依赖项注入：获取 PostgreSQL 数据库会话对象
async def get_pg_db():
    async with PgSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise e
        finally:
            await session.close()
