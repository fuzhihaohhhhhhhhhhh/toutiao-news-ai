from typing import Optional

from langchain_core.documents import Document

from rag.vector import VectorService
from utils.config_handler import chroma_conf
from utils.logger_handler import logger

#检索服务：基于 Chroma 向量库检索相关新闻
class RetrieverService:
    def __init__(self):
        """初始化时复用 VectorService 的 vector_store"""
        self.vector_service = VectorService()
        # 直接用 vector_store 做检索，比 as_retriever 更灵活（可动态调 k）
        self.vector_store = self.vector_service.vector_store

    def search(self, query: str, k: Optional[int] = None) -> list[Document]:
        """
        检索相关新闻片段（语义相关中按发布时间从新到旧取前 k 条）
        :param query:
        :param k:
        :return:
        """
        # 没传 k 就用配置文件里的默认值
        if k is None:
            k = chroma_conf["k"]

        try:
            # 第一阶段：多召回一些语义相关候选（3 倍池子）
            fetch_k = k * 3
            docs = self.vector_store.similarity_search(query, k=fetch_k)
            # 第二阶段：按发布时间从新到旧排序，取前 k 条
            # publish_time 格式为 "YYYY-MM-DD HH:MM:SS"（ISO 风格），
            # 字符串字典序与时间序一致，直接字符串排序即可；
            # 缺失/异常值用空字符串兜底，reverse=True 时自然排到最后
            docs.sort(
                key=lambda d: d.metadata.get("publish_time")
                if isinstance(d.metadata.get("publish_time"), str)
                else "",
                reverse=True,
            )
            docs = docs[:k]
            logger.info(f"[检索] 查询=[{query}] 召回{fetch_k}条 时间排序后取{len(docs)}条")
            return docs
        except Exception as e:
            logger.error(f"[检索] 失败 query=[{query}]: {type(e).__name__}: {e}")
            return []

    def search_with_scores(self, query: str, k: Optional[int] = None) -> list[tuple[Document, float]]:
        """
        检索并返回相似度分数
        :param query
        :param
        :return:
        """
        if k is None:
            k = chroma_conf["k"]

        try:
            results = self.vector_store.similarity_search_with_score(query, k=k)
            logger.info(f"[检索] 带分数查询=[{query}] 命中 {len(results)} 条")
            return results
        except Exception as e:
            logger.error(f"[检索] 带分数查询失败 query=[{query}]: {type(e).__name__}: {e}")
            return []
