import os
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Optional
from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from utils.config_handler import rag_conf
from utils.path_tool import get_abs_path
load_dotenv(get_abs_path(".env"))

api_key = os.getenv("DASHSCOPE_API_KEY")
base_url = os.getenv("DASHSCOPE_BASE_URL")


def assert_keys():
    """调用模型前校验 key，避免 key 缺失时实例化不报错、调用才崩、难定位"""
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置，请检查 .env 文件")
    if not base_url:
        raise RuntimeError("DASHSCOPE_BASE_URL 未配置，请检查 .env 文件")


class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        pass

def _thinking_kwargs() -> dict:
    """
    控制 qwen3 系列的「思考模式」。

    qwen3 系默认开着思考：回答前先想很久，思考的 token 会流式吐出来、但 content 是空的。
    结果就是——用户感受到的首字延迟基本等于思考时间，跟网络快慢没关系。
    同一段真实长度的 prompt 实测：
        带思考   → 首字 3922 ms，26 个空 chunk（评测里采样到过 12~30 秒）
        关掉思考 → 首字  721 ms， 4 个空 chunk
    开关放在 config/rag.yml 的 disable_thinking，需要长链条推理时可以关掉。
    """
    if rag_conf.get("disable_thinking", True):
        return {"extra_body": {"enable_thinking": False}}
    return {}


class ChatModelFactory(BaseModelFactory):
    """生成回答用的模型（质量优先）"""
    def generator(self) -> BaseChatModel:
        return ChatOpenAI(
            model=rag_conf["chat_model_name"],
            api_key=api_key,
            base_url=base_url,
            **_thinking_kwargs(),
        )


class FastChatModelFactory(BaseModelFactory):
    """
    短输出场景用的模型（速度优先）。

    意图分类只需要吐出一个词（chitchat / news_qa / report），
    用 max 档还要先思考 3~5 秒纯属浪费，换成 turbo 档。
    """
    def generator(self) -> BaseChatModel:
        return ChatOpenAI(
            model=rag_conf.get("fast_model_name") or rag_conf["chat_model_name"],
            api_key=api_key,
            base_url=base_url,
            **_thinking_kwargs(),
        )

class EmbeddingsFactory(BaseModelFactory):
    def generator(self) -> Embeddings:
        return OpenAIEmbeddings(
            model=rag_conf["embedding_model_name"],
            api_key=api_key,
            base_url=base_url,
            # DashScope 兼容模式必须关闭，否则按 OpenAI tiktoken 规则切分文本会出错
            check_embedding_ctx_length=False,
        )


@lru_cache(None)
def get_chat_model() -> BaseChatModel:
    """延迟创建 chat 模型（生成回答用），全局单例；key 缺失时立即报错"""
    assert_keys()
    return ChatModelFactory().generator()


@lru_cache(None)
def get_fast_model() -> BaseChatModel:
    """延迟创建快模型（意图分类等短输出场景），全局单例；key 缺失时立即报错"""
    assert_keys()
    return FastChatModelFactory().generator()


@lru_cache(None)
def get_embed_model() -> Embeddings:
    """延迟创建 embedding 模型，全局单例；key 缺失时立即报错"""
    assert_keys()
    return EmbeddingsFactory().generator()
