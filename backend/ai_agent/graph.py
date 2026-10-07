from typing import TypedDict
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from models.factory import get_chat_model, get_fast_model
from rag.retriever import RetrieverService
from ai_agent.tool.agent_tool import get_current_date, get_current_month, get_monthly_reading_stats
from utils.path_tool import get_abs_path
from utils.logger_handler import logger


class AgentState(TypedDict):
    messages: list
    user_id: int
    intent: str


def load_prompt(relative_path: str) -> str:
    """
    从项目根目录加载 prompt 文本文件
    """
    path = get_abs_path(relative_path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


async def classify_node(state: AgentState) -> dict:
    user_msg = state["messages"][-1].content

    # 带上最近几条对话上下文（截断防撑爆 token），
    # 否则"那体育呢？"这类追问单独看无法正确分类
    recent = state["messages"][-5:-1]  # 不含当前消息
    history_text = "\n".join(
        f"{'用户' if isinstance(m, HumanMessage) else 'AI'}：{m.content[:80]}"
        for m in recent
    ) or "（无，这是本轮对话第一句）"

    # 分类 prompt：让 LLM 只输出意图标签，不要其他内容
    classify_prompt = """判断用户最新消息的意图，只返回以下三个英文单词之一，不要返回其他任何内容：

- chitchat：闲聊问候（如打招呼、询问你是谁、感谢、与新闻无关的日常交流）
- news_qa：新闻资讯问答（询问新闻事件、热点资讯、"今天有什么新闻"等）
- report：生成或查询月度阅读报告（如"生成我的阅读报告""我本月看了多少新闻"）

参考规则：
- 结合最近对话理解追问（历史里在聊新闻，用户说"那体育呢？"属于 news_qa）
- 消息里同时含闲聊和新闻询问时，以核心需求为准（如"你好，今天有什么新闻"是 news_qa）

最近对话：
{history}

用户最新消息：{msg}

意图："""

    # 分类只需要输出一个词，用快模型：max 档会先思考 3~5 秒，纯属浪费
    llm = get_fast_model()
    response = await llm.ainvoke(classify_prompt.format(history=history_text, msg=user_msg))
    # 去掉首尾空格并转换为小写
    intent = response.content.strip().lower()

    # 容错：只取第一个词（LLM 可能多输出了内容）
    intent = intent.split()[0] if intent.split() else "chitchat"
    # 未知意图默认走闲聊（最安全的兜底）
    if intent not in ("chitchat", "news_qa", "report"):
        intent = "chitchat"

    logger.info(f"[意图分类] 消息=[{user_msg[:30]}...] 意图={intent}")
    return {"intent": intent}


async def chitchat_node(state: AgentState) -> dict:
    """
    闲聊节点：用 main_prompt 作为系统提示，结合完整对话历史回答
    """
    # 加载主 prompt 作为系统提示（里面有闲聊的指引）
    system_prompt = load_prompt("prompts/main_prompt.txt")

    llm = get_chat_model()
    # 传入完整对话历史（含当前消息），多轮记忆才能生效
    # （此前只传最后一条消息，导致"记住我叫小明"这类信息 AI 看不到）
    response = await llm.ainvoke(
        [SystemMessage(content=system_prompt)] + state["messages"]
    )

    logger.info(f"[闲聊] 回复={response.content[:50]}...")
    return {"messages": [response]}


async def news_qa_node(state: AgentState) -> dict:
    """
    新闻问答节点：检索新闻 → 构造上下文 → RAG 生成
    """
    user_msg = state["messages"][-1]
    query = user_msg.content

    # a. 获取当天日期（get_current_date 是 @tool，invoke 是同步调用）
    today = get_current_date.invoke({})

    # b. 检索新闻（RetrieverService.search 是同步方法，直接调）
    retriever = RetrieverService()
    docs = retriever.search(query)

    # 构造上下文：把每条检索结果拼成文本，带上发布时间
    context_parts = []
    for d in docs:
        publish_time = d.metadata.get("publish_time", "未知")
        context_parts.append(f"【发布时间：{publish_time}】\n{d.page_content}")
    context = "\n\n".join(context_parts) if context_parts else "无相关新闻资料"

    # c/d/e/f. 用 rag_summarize prompt + 当天日期 + 上下文 → LLM 生成
    rag_prompt = load_prompt("prompts/rag_summarize.txt")
    # rag_summarize.txt 有 {input} 和 {context} 两个占位符
    system_content = rag_prompt.format(input=query, context=context)
    # 额外注入当天日期 + 回退话术指引，让 LLM 能对比 publish_time 判断当天有无新闻
    system_content += (
        f"\n\n### 当天日期\n{today}\n"
        f"补充说明：\n"
        f"1. 上述新闻资料已按发布时间从新到旧排列，资料中第一条即为该话题下最新的新闻；\n"
        f"2. 请对比新闻发布时间与当天日期，判断是否存在当天发布的新闻。"
        f"若当天无新新闻发布，请以话术「当天未检测到有新的相关新闻发布哦，"
        f"已为您检索到最近的相关新闻信息：」开头，"
        f"按发布时间从新到旧列出资料中的相关新闻（含标题、发布时间、核心内容摘要）；\n"
        f"3. 若用户提问指定了时间段（如某年份、某月份），而资料中没有该时间段的新闻，"
        f"必须如实告知知识库中暂无该时间段的新闻，并给出资料中实际最新的新闻发布时间，"
        f"严禁把其他时间段的新闻当作该时间段的内容输出。"
    )

    llm = get_chat_model()
    # 生成时传入完整对话历史（含当前消息）：
    # 用户追问"那体育呢？"时，LLM 能结合上文理解完整问题，
    # 检索词（query）本身也包含"体育"关键词，检索不受影响
    response = await llm.ainvoke(
        [SystemMessage(content=system_content)] + state["messages"]
    )

    logger.info(f"[新闻问答] 回复={response.content[:50]}...")
    return {"messages": [response]}


async def report_node(state: AgentState) -> dict:
    """
    月度报告节点：统计本月阅读数据 → RAG 检索拓展资料 → LLM 生成报告
    """
    user_id = state["user_id"]

    # 1. 当前月份（同步工具，invoke 调用）
    month = get_current_month.invoke({})

    # 2. 本月阅读统计（异步工具，ainvoke 调用）
    stats = await get_monthly_reading_stats.ainvoke({"user_id": user_id, "month": month})

    # 3. 无记录兜底：直接回复引导话术
    if "无浏览记录" in stats or "统计失败" in stats:
        msg = (
            f"您在 {month} 暂无新闻浏览记录哦～先去读几篇感兴趣的新闻，"
            f"下个月再来生成阅读报告吧！"
        )
        logger.info(f"[月度报告] user_id={user_id} {month} 无数据，返回引导话术")
        return {"messages": [AIMessage(content=msg)]}

    # 4. RAG 拓展资料：用 Top1 分类名检索相关热点新闻（让推荐建议更有依据）
    rag_material = ""
    try:
        # 从统计文本解析 Top1 分类名（格式："分类分布Top3：科技（5篇）、..."）
        top_category = stats.split("分类分布Top3：")[1].split("（")[0].strip()
        retriever = RetrieverService()
        docs = retriever.search(top_category)
        if docs:
            rag_material = "\n".join(f"- {d.page_content[:150]}" for d in docs[:3])
        logger.info(f"[月度报告] 拓展资料检索完成 检索词=[{top_category}] 条数={len(docs)}")
    except Exception as e:
        # 拓展资料只是锦上添花，检索失败不影响报告生成
        logger.warning(f"[月度报告] RAG 检索失败，跳过拓展资料：{type(e).__name__}: {e}")

    # 5. 拼 prompt → LLM 生成报告
    report_prompt = load_prompt("prompts/report_prompt.txt")
    # report_prompt.txt 有 {user_id}、{month}、{stats} 三个占位符
    system_content = report_prompt.format(user_id=user_id, month=month, stats=stats)
    if rag_material:
        system_content += f"\n\n### 拓展阅读参考资料\n{rag_material}"

    llm = get_chat_model()
    # 传入完整对话历史：用户附加的个性化要求（如"重点分析科技类偏好"）也能生效
    response = await llm.ainvoke(
        [SystemMessage(content=system_content)] + state["messages"]
    )

    logger.info(f"[月度报告] user_id={user_id} 报告生成完成 长度={len(response.content)}")
    return {"messages": [response]}


def route_by_intent(state: AgentState) -> str:
    """
    条件边路由函数：根据 classify_node 输出的 intent 决定走哪个分支
    """
    intent = state.get("intent", "chitchat")
    if intent == "news_qa":
        return "news_qa"
    elif intent == "report":
        return "report"
    else:
        return "chitchat"


def build_graph():
    workflow = StateGraph(AgentState)

    # ── 添加节点 ──
    workflow.add_node("classify", classify_node)       # 意图分类
    workflow.add_node("chitchat", chitchat_node)        # 闲聊分支
    workflow.add_node("news_qa", news_qa_node)          # 新闻问答分支
    workflow.add_node("report", report_node)            # 月度报告分支

    # ── 添加边 ──
    # 起点 → 分类节点
    workflow.add_edge(START, "classify")

    # 分类节点 → 条件边（根据 intent 路由到三个分支之一）
    workflow.add_conditional_edges(
        "classify",
        route_by_intent,
        {
            "chitchat": "chitchat",
            "news_qa": "news_qa",
            "report": "report",
        },
    )

    # 三个分支各自结束后走向 END
    workflow.add_edge("chitchat", END)
    workflow.add_edge("news_qa", END)
    workflow.add_edge("report", END)

    # 编译并返回可执行的图
    return workflow.compile()

if __name__ == "__main__":
    from IPython.display import display
    display(build_graph())
