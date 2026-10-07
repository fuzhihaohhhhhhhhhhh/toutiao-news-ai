from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config.env import env, env_int

# ── 连接参数全部来自环境变量 ──
# 真实凭据只写在本地 .env（不入库），模板见项目根目录 .env.example
DB_HOST = env("MYSQL_HOST", "localhost")
DB_PORT = env_int("MYSQL_PORT", 3306)
DB_USER = env("MYSQL_USER", "root")
DB_PASSWORD = env("MYSQL_PASSWORD", "")
DB_NAME = env("MYSQL_DB", "news_app")

# quote_plus：密码里若含 @ : / 等字符，不做转义会把连接串拆坏
ASYNC_DATABASE_URL = (
    f"mysql+aiomysql://{DB_USER}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
)

# 创建异步数据库引擎
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    # echo=True 会把每一条 SQL 连同参数打到控制台。
    # 调试 SQL 时可以临时改回 True，但别提交上去 —— 它会让日志爆量，
    # 而且参数里会带上手机号、密码哈希这类敏感值。
    # 这里和 config/pg_config.py 的 echo=False 保持一致。
    echo=False,
    pool_size=10,#设置连接池活跃的连接数
    max_overflow=20,#允许额外的连接数
    )

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,#绑定异步引擎
    class_=AsyncSession,#指定会话类
    expire_on_commit=False,#提交后不会过期，不会重新查询数据库
)

#依赖项注入：获取数据库会话对象
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session #yield:先暂停，把后面的值“交出去”，等调用方处理完再恢复回来继续往下执行。返回数据库会话给路由处理函数
            await session.commit() #await:提交事务需要等数据库响应
        except Exception as e:
            await session.rollback() #如果发生异常，就回滚，await:回滚需要等数据库响应
            raise e #抛出异常，让路由处理函数处理
        finally:
            await session.close() #关闭数据库会话
