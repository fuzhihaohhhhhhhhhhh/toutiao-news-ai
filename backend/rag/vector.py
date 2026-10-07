import asyncio
import json
from langchain_core.documents import Document
from cache.embedding_cache import get_cached_embed_model
from models.news import News
from utils.config_handler import chroma_conf
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from crud.agent import get_all_news
from utils.file_handler import get_text_md5_hex
from utils.logger_handler import logger
from utils.path_tool import get_abs_path
from config.db_config import AsyncSessionLocal

# DashScope 向量接口单批上限
BATCH_SIZE = 10


def load_md5_store() -> dict:
    #读取已索引新闻的 md5 指纹（JSON dict 格式：{news_id: md5_hex}）
    path = get_abs_path(chroma_conf["md5_hex_store"])# md5.text
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_md5_store(store: dict):
    #保存 md5 指纹到本地
    path = get_abs_path(chroma_conf["md5_hex_store"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def build_doc(news: News) -> Document:
    content = f"标题：{news.title}\n简介：{news.description or ''}\n正文：{news.content}"
    return Document(
        page_content=content,
        metadata={
            "news_id": news.id,
            "title": news.title,
            "category_id": news.category_id,
            "author": news.author or "",
            "publish_time": news.publish_time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


class VectorService:
    def __init__(self):
        self.vector_store = Chroma(
            collection_name=chroma_conf["collection_name"],  # 向量数据库集合名称
            embedding_function=get_cached_embed_model(),  # 带缓存的 Embeddings，省 token
            # 持久化目录。Chroma 默认是内存数据库，重启即丢。指定这个目录后，向量数据会被保存到硬盘，下次启动时自动加载。
            persist_directory=get_abs_path(chroma_conf["persist_directory"]),
        )
        # 递归字符文本分割器，将文档分割成多个小段
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chroma_conf["chunk_size"],
            # 重叠长度。用于在不同段落之间平滑过渡。
            chunk_overlap=chroma_conf["chunk_overlap"],
            # 分隔符。用于将文档分割成多个段落。
            separators=chroma_conf["separators"],
            # 长度函数。用于计算分割段落的数量。
            length_function=len,
        )

    def get_retriever(self):
        # 将一个向量数据库客户端包装成LangChain标准的检索器（Retriever）。包装后，你可以统一调用.invoke(query)
        # 进行语义搜索。
        return self.vector_store.as_retriever(search_kwargs={"k": chroma_conf["k"]})  # k 表示每次查询返回最多 k 个最相似的文档

    async def load_document(self):
        """
        构建知识库（异步）：读取全部新闻 → md5 增量判断 → 切分 → 写入 Chroma
        调用方式：在 async 函数里用 await service.load_document()；
        命令行直接调用请用 load_document_sync()。
        """

        md5_store = load_md5_store()
        indexed, skipped = 0, 0
        offset = 0 # 跳过数量
        limit = 500  # 分页加载，避免一次性全表加载到内存

        while True:
            async with AsyncSessionLocal() as db:
                news_list = await get_all_news(db, offset=offset, limit=limit)
            if not news_list:
                break

            for news in news_list:
                doc = build_doc(news)
                current_md5 = get_text_md5_hex(doc.page_content)
                # 内容未变化：跳过，省时间省算力
                if md5_store.get(str(news.id)) == current_md5:
                    skipped += 1
                    continue

                # 内容有变化：先删掉该新闻的旧片段再重写
                self.vector_store._collection.delete(where={"news_id": news.id})
                chunks = self.splitter.split_documents([doc])
                for i in range(0, len(chunks), BATCH_SIZE):
                    self.vector_store.add_documents(chunks[i:i + BATCH_SIZE])

                md5_store[str(news.id)] = current_md5
                indexed += 1
                logger.info(f"[知识库] 已索引 id={news.id} 《{news.title}》 片段数={len(chunks)}")

            offset += limit

        save_md5_store(md5_store)
        logger.info(f"[知识库] 构建完成：新增/更新 {indexed} 篇，跳过 {skipped} 篇")

    def load_document_sync(self):
        """同步包装：供命令行直接调用（如 python -c 或 __main__ 中）"""
        asyncio.run(self.load_document())
