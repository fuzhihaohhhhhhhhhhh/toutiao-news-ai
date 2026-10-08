"""
全局异常处理器

四个处理器对应 main.py 里注册的四类异常：

    HTTPException   —— 业务层主动抛出（401 / 404 / …）
    IntegrityError  —— 数据库唯一约束冲突、外键不存在
    SQLAlchemyError —— 其他数据库错误
    Exception       —— 兜底

统一约定：

1. 响应体始终是 ``{code, message, data}``，与 utils/response.py 的成功响应同形，
   前端只需判断 ``code`` 与读 ``message``。
2. **异常细节只写日志，不回传给客户端**。原始报错里有表名、索引名、SQL 片段、
   文件路径这些内部信息，回传给任何调用方都属于信息泄露 —— 详见下面 DEBUG_MODE。
3. 所有异常都会进日志文件 ``backend/logs/agent_YYYYMMDD.log``。
   此前四个处理器一条日志都不写，导致 401 之类的认证失败在日志里完全不可见，
   只能靠手工查库定位。
"""
import traceback

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette import status

from config.env import env
from utils.logger_handler import logger

# 是否把异常细节（错误类型 / 原始信息 / 堆栈）一并放进响应体的 data 字段。
#
# ⚠️ 默认关闭。开启后任何调用方都能从响应里读到服务器内部堆栈，
# 属于信息泄露，仅限本地排查使用。排查问题请优先看日志文件：
#     backend/logs/agent_YYYYMMDD.log
# 确有需要在响应里调试时，在 .env 里加一行：DEBUG_ERROR_RESPONSE=true
DEBUG_MODE = env("DEBUG_ERROR_RESPONSE", "").strip().lower() in ("1", "true", "yes", "on")


def _client_ip(request: Request) -> str:
    """取客户端 IP。某些测试客户端下 request.client 可能为 None"""
    return request.client.host if request.client else "unknown"


def _debug_data(request: Request, err_type: str, detail: str):
    """
    构造回传给客户端的调试信息；DEBUG_MODE 关闭时返回 None。

    抽成一个函数是为了让四个处理器口径一致，避免某处漏加判断。
    """
    if not DEBUG_MODE:
        return None
    return {
        "error_type": err_type,
        "error_detail": detail,
        "traceback": traceback.format_exc(),
        "path": str(request.url),
    }


async def http_exception_handler(request: Request, exc: HTTPException):
    """
    处理 HTTPException（业务层主动抛出）

    日志分级：
      401 / 403 / 429 属**安全相关事件**（认证失败、越权、限流），用 WARNING，
      这类事件正是运维最需要审计的；
      其余（404 / 400 等）是正常业务分支，用 INFO，避免把日志刷成噪声。
    """
    line = (
        f"[HTTP {exc.status_code}] {_client_ip(request)} "
        f"{request.method} {request.url.path} - {exc.detail}"
    )
    if exc.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_429_TOO_MANY_REQUESTS,
    ):
        logger.warning(line)
    else:
        logger.info(line)

    # HTTPException 的 detail 是给用户看的业务文案（如"用户名已存在"、"新闻不存在"），
    # 不含内部实现信息，所以直接作为 message 透出；data 保持 None。
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail,
            "data": None,
        },
    )


async def integrity_error_handler(request: Request, exc: IntegrityError):
    """处理数据库完整性约束错误（唯一键冲突、外键不存在）"""
    error_msg = str(exc.orig)

    # 把数据库的原始报错翻译成用户能懂的话
    if "username_UNIQUE" in error_msg or "Duplicate entry" in error_msg:
        detail = "用户名已存在"
    elif "FOREIGN KEY" in error_msg:
        detail = "关联数据不存在"
    else:
        detail = "数据约束冲突，请检查输入"

    # 原始报错含表名 / 索引名，只进日志
    logger.warning(
        f"[DB 约束冲突] {_client_ip(request)} {request.method} {request.url.path} - {error_msg}"
    )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "code": 400,
            "message": detail,
            "data": _debug_data(request, "IntegrityError", error_msg),
        },
    )


async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    """处理 SQLAlchemy 数据库错误（连接失败、SQL 语法错等）"""
    # exc_info=exc 让日志自动带上完整堆栈，比手工 traceback.format_exc() 可靠：
    # 异常处理器执行时并不在 except 块里，sys.exc_info() 可能已经空了。
    logger.error(
        f"[DB 错误] {_client_ip(request)} {request.method} {request.url.path} - "
        f"{type(exc).__name__}: {exc}",
        exc_info=exc,
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "code": 500,
            "message": "数据库操作失败，请稍后重试",
            "data": _debug_data(request, type(exc).__name__, str(exc)),
        },
    )


async def general_exception_handler(request: Request, exc: Exception):
    """兜底：处理所有未被前面三类覆盖的异常"""
    logger.error(
        f"[未捕获异常] {_client_ip(request)} {request.method} {request.url.path} - "
        f"{type(exc).__name__}: {exc}",
        exc_info=exc,
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "code": 500,
            "message": "服务器内部错误",
            "data": _debug_data(request, type(exc).__name__, str(exc)),
        },
    )
