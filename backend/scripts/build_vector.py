"""
构建 / 更新站内新闻向量库

用途：
    把 MySQL 里 news 表的所有新闻切分后写入 Chroma 向量库（chroma_db/），
    并把每篇新闻内容的 MD5 记到 md5.text。AI 问答（news_qa 链路）依赖它。

    chroma_db/ 与 md5.text 都是**可从数据库再生的派生数据**，因此没有进版本库。
    首次部署（尤其是 Docker）后必须跑一次。

本地运行（工作目录要在 backend/）：
    python scripts/build_vector.py

Docker 里运行：
    docker compose exec backend python scripts/build_vector.py

增量特性：
    脚本会比对每篇新闻内容的 MD5，**只重建内容有变化的新闻**。
    所以可以反复执行，第二次起会很快（全部跳过）。

注意：
    需要 MySQL 可连、且 .env 里的 DASHSCOPE_API_KEY 有效 ——
    写向量时要调用 DashScope 的 embedding 接口（会产生少量费用）。
"""
import asyncio
import os
import sys

# 允许直接以 `python scripts/build_vector.py` 运行：
# 把项目根目录（本文件所在目录的上一级）加入模块搜索路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.db_config import async_engine  # noqa: E402
from rag.vector import VectorService  # noqa: E402
from utils.logger_handler import logger  # noqa: E402


async def main() -> None:
    logger.info("[向量库] 开始构建……")
    service = VectorService()
    try:
        await service.load_document()
    finally:
        # 显式释放连接池：否则脚本退出时事件循环已关闭，
        # 连接回收会抛 "Event loop is closed"，虽然不影响结果但日志一片红
        await async_engine.dispose()
    logger.info("[向量库] 构建结束")


if __name__ == "__main__":
    asyncio.run(main())
