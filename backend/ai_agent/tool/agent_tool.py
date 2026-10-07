"""
Agent 工具函数集合
工具清单（当前）：
1. get_current_date          —— 获取系统当天日期（YYYY-MM-DD）
2. get_user_monthly_history  —— 查询指定用户最近一个月的浏览记录
3. get_current_month         —— 获取系统当前月份（YYYY-MM）
4. get_monthly_reading_stats —— 统计指定用户指定月份的阅读数据（报告用）
"""
from collections import Counter
from datetime import datetime, timedelta
from typing import Annotated

from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from config.db_config import AsyncSessionLocal
from models.history import History
from models.news import News, Category
from utils.logger_handler import logger

@tool
def get_current_date() -> str:
    """获取系统当前日期（当天），格式固定为 YYYY-MM-DD（如 2026-09-03）。无入参。"""
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"[工具] get_current_date 调用，返回 {today}")
    return today


@tool
async def get_user_monthly_history(user_id: Annotated[int, "当前登录用户ID，由系统自动注入，无需询问用户"]) -> str:
    """查询指定用户最近一个月（近30天）的新闻浏览记录，按浏览时间倒序返回。入参 user_id 为数字，由系统自动注入。"""
    # 计算30天前的时间点
    one_month_ago = datetime.now() - timedelta(days=30)

    try:
        # 用 async session 查询，History 表 join News 表拿到新闻标题等信息
        async with AsyncSessionLocal() as db:
            # 联表查询：浏览历史 + 新闻详情
            # History.news_id == News.id 把两张表关联起来
            # 筛选条件：当前用户 + 30天内
            # 排序：按浏览时间倒序（最近的在前）
            # 限制：最多取 20 条，避免返回过多数据撑爆 LLM 上下文
            query = (
                select(History, News)
                .join(News, History.news_id == News.id)
                .where(
                    History.user_id == user_id,
                    History.view_time >= one_month_ago,
                )
                .order_by(History.view_time.desc())
                .limit(20)
            )
            result = await db.execute(query)
            # result.all() 返回 [(History, News), ...] 元组列表
            rows = result.all()

        if not rows:
            logger.info(f"[工具] get_user_monthly_history user_id={user_id} 无记录")
            return "最近一个月无浏览记录"

        # 拼装成 LLM 易读的结构化文本
        lines = []# 每条记录占一行，格式为：[时间] 新闻标题（分类ID:新闻ID）
        for history, news in rows:
            lines.append(
                f"- [{history.view_time.strftime('%Y-%m-%d %H:%M')}] "
                f"{news.title}（分类ID:{news.category_id}, 新闻ID:{news.id}）"
            )

        result_str = "\n".join(lines)
        logger.info(f"[工具] get_user_monthly_history user_id={user_id} 返回 {len(rows)} 条记录")
        return result_str

    except Exception as e:
        logger.error(f"[工具] get_user_monthly_history 查询失败 user_id={user_id}: {type(e).__name__}: {e}")
        return "查询浏览记录失败，请稍后重试"


@tool
def get_current_month() -> str:
    """获取系统当前月份，格式固定为 YYYY-MM（如 2026-09）。无入参。"""
    month = datetime.now().strftime("%Y-%m")
    logger.info(f"[工具] get_current_month 调用，返回 {month}")
    return month


def month_range(month: str) -> tuple[datetime, datetime]:
    """
    把 YYYY-MM 字符串解析成 [月初, 次月初) 左闭右开时间区间

    :param month: 月份字符串，如 "2026-09"
    :return: (月初 datetime, 次月初 datetime)
    """
    year, mon = map(int, month.split("-"))
    start = datetime(year, mon, 1)
    # 12月特殊处理：次月是明年1月
    end = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)
    return start, end


@tool
async def get_monthly_reading_stats(
    user_id: Annotated[int, "当前登录用户ID，由系统自动注入，无需询问用户"],
    month: Annotated[str, "统计月份，YYYY-MM 格式（如 2026-09），由系统自动注入"],
) -> str:
    """统计指定用户在指定月份的新闻阅读数据，返回阅读总篇数、活跃天数、分类分布Top3、最近一周浏览话题。入参 user_id 为数字、month 为 YYYY-MM 格式字符串，均由系统自动注入。"""
    start, end = month_range(month)

    try:
        # 联表查询：浏览历史 + 新闻标题 + 分类名称（一次查询拿到报告全部所需数据）
        async with AsyncSessionLocal() as db:
            query = (
                select(History, News.title, Category.name)
                .join(News, History.news_id == News.id)
                .join(Category, News.category_id == Category.id)
                .where(
                    History.user_id == user_id,
                    History.view_time >= start,
                    History.view_time < end,
                )
                .order_by(History.view_time.desc())
            )
            result = await db.execute(query)
            rows = result.all()

        if not rows:
            logger.info(f"[工具] get_monthly_reading_stats user_id={user_id} month={month} 无记录")
            return f"该月（{month}）无浏览记录"

        # 阅读总篇数（History 按 user+news 唯一，一条记录即一篇）
        total = len(rows)
        # 活跃天数：浏览时间去重后的天数
        active_days = len({h.view_time.date() for h, _, _ in rows})
        # 分类分布Top3：按分类名计数，取前3
        category_counter = Counter(name for _, _, name in rows)
        top3 = "、".join(
            f"{name}（{count}篇）" for name, count in category_counter.most_common(3)
        )
        # 最近一周浏览话题：7天内记录的标题（最多10条，防撑爆上下文）
        week_ago = datetime.now() - timedelta(days=7)
        week_rows = [(h, title) for h, title, _ in rows if h.view_time >= week_ago][:10]
        week_topics = "\n".join(
            f"- [{h.view_time.strftime('%Y-%m-%d')}] {title}" for h, title in week_rows
        ) or "- 最近一周无浏览"

        stats_str = (
            f"阅读总篇数：{total} 篇\n"
            f"活跃天数：{active_days} 天\n"
            f"分类分布Top3：{top3}\n"
            f"最近一周浏览话题：\n{week_topics}"
        )
        logger.info(
            f"[工具] get_monthly_reading_stats user_id={user_id} month={month} "
            f"总篇数={total} 活跃天数={active_days}"
        )
        return stats_str

    except Exception as e:
        logger.error(
            f"[工具] get_monthly_reading_stats 统计失败 user_id={user_id} month={month}: "
            f"{type(e).__name__}: {e}"
        )
        return "阅读数据统计失败，请稍后重试"


agent_tools = [get_current_date, get_user_monthly_history, get_current_month, get_monthly_reading_stats]
