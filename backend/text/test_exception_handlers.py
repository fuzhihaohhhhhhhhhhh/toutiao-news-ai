"""
异常处理器单元测试

与其他测试文件不同，这个文件**不需要任何外部服务**（不需要 MySQL / PostgreSQL /
Redis，也不需要先启动 8000 服务），它直接构造 Request 调用四个处理器。
因此它可以在任何环境、任何 CI 里稳定运行。

验证两件事：

1. **响应契约**：始终是 ``{code, message, data}``；内部细节（数据库原始报错、
   堆栈）只进日志，**不回传给客户端**。
2. **日志分级**：401 / 403 / 429 属安全事件记 WARNING，其余 HTTP 异常记 INFO，
   5xx 记 ERROR 并带堆栈。
"""
import asyncio
import json
import logging
import os
import sys

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.requests import Request

# 允许从任意目录运行（与 text/test_smoke.py 的做法一致）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from utils.exception import (  # noqa: E402
    DEBUG_MODE,
    general_exception_handler,
    http_exception_handler,
    integrity_error_handler,
    sqlalchemy_error_handler,
)

LOGGER_NAME = "agent"


def _request(method: str = "GET", path: str = "/api/x", ip: str = "203.0.113.7") -> Request:
    """构造一个最小的 Request，用于喂给异常处理器"""
    return Request({
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("127.0.0.1", 8000),
        "client": (ip, 54321),
    })


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _call(handler, request, exc):
    return asyncio.run(handler(request, exc))


# ──────────────────────────── 响应契约 ────────────────────────────

def test_http异常响应体是统一三字段结构():
    resp = _call(http_exception_handler, _request(), HTTPException(status_code=404, detail="新闻不存在"))
    b = _body(resp)
    assert resp.status_code == 404
    assert set(b) == {"code", "message", "data"}
    assert b["code"] == 404
    assert b["message"] == "新闻不存在"
    assert b["data"] is None


def test_数据库约束错误翻译成中文且不回传原始报错():
    exc = IntegrityError("stmt", {}, Exception("Duplicate entry 'x' for key 'username_UNIQUE'"))
    resp = _call(integrity_error_handler, _request(), exc)
    b = _body(resp)
    assert resp.status_code == 400
    assert b["message"] == "用户名已存在"
    # 原始报错里有表名 / 索引名，绝不能出现在响应里
    assert b["data"] is None


def test_外键冲突翻译成关联数据不存在():
    exc = IntegrityError("stmt", {}, Exception("FOREIGN KEY constraint failed"))
    resp = _call(integrity_error_handler, _request(), exc)
    assert _body(resp)["message"] == "关联数据不存在"


def test_数据库错误返回固定文案且不回传堆栈():
    resp = _call(sqlalchemy_error_handler, _request(), OperationalError("x", {}, Exception("boom")))
    b = _body(resp)
    assert resp.status_code == 500
    assert b["message"] == "数据库操作失败，请稍后重试"
    assert b["data"] is None


def test_兜底异常返回固定文案且不回传堆栈():
    resp = _call(general_exception_handler, _request(), ConnectionRefusedError("拒绝连接"))
    b = _body(resp)
    assert resp.status_code == 500
    assert b["message"] == "服务器内部错误"
    assert b["data"] is None


def test_debug_mode_默认关闭():
    """默认必须关闭 —— 打开后异常细节会随响应回传给任何调用方"""
    assert DEBUG_MODE is False


# ──────────────────────────── 日志行为 ────────────────────────────

def test_认证失败记_warning_且带客户端_ip(caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        _call(http_exception_handler, _request("POST", "/api/user/login"),
              HTTPException(status_code=401, detail="用户名或密码错误"))

    hits = [r for r in caplog.records if "[HTTP 401]" in r.getMessage()]
    assert hits, "401 必须留下日志（此前四个处理器一条日志都不写）"
    assert hits[0].levelno == logging.WARNING, "认证失败属安全事件，应为 WARNING"
    assert "203.0.113.7" in hits[0].getMessage(), "应记录客户端 IP 便于审计"


@pytest.mark.parametrize("code", [403, 429])
def test_越权与限流也记_warning(caplog, code):
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        _call(http_exception_handler, _request(), HTTPException(status_code=code, detail="x"))
    hits = [r for r in caplog.records if f"[HTTP {code}]" in r.getMessage()]
    assert hits and hits[0].levelno == logging.WARNING


def test_普通业务分支记_info_不刷成噪声(caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        _call(http_exception_handler, _request(), HTTPException(status_code=404, detail="新闻不存在"))

    hits = [r for r in caplog.records if "[HTTP 404]" in r.getMessage()]
    assert hits and hits[0].levelno == logging.INFO
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], "404 不应升级成 WARNING"


def test_服务器错误记_error_且带堆栈(caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        _call(general_exception_handler, _request("POST", "/api/chat/session"),
              ConnectionRefusedError("拒绝连接"))

    hits = [r for r in caplog.records if "ConnectionRefusedError" in r.getMessage()]
    assert hits, "未捕获异常必须进日志"
    assert hits[0].levelno == logging.ERROR
    # exc_info 带上了异常对象，日志里才会出现完整堆栈
    assert hits[0].exc_info is not None


def test_原始数据库报错进日志而非响应(caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        exc = IntegrityError("stmt", {}, Exception("Duplicate entry 'x' for key 'username_UNIQUE'"))
        resp = _call(integrity_error_handler, _request(), exc)

    assert "username_UNIQUE" in caplog.text, "原始报错应进日志以便排查"
    assert "username_UNIQUE" not in resp.body.decode("utf-8"), "原始报错不应进响应"
