# ════════════════════════════════════════════════════════════════
# 后端镜像：FastAPI + LangGraph RAG
#
# 构建上下文是 backend/ 目录（见 docker-compose.yml 的 build.context）。
# backend/.dockerignore 会把 .env、chroma_db、日志等排除在外 ——
# 尤其是 .env，它绝不能被 COPY 进镜像层，否则密钥会被永久烤进镜像。
# ════════════════════════════════════════════════════════════════
FROM python:3.13-slim

# ── 时区（必须设置）──
# 项目大量使用 datetime.now()：token 过期时间、"今天有什么新闻"的当天日期、
# 月度报告的月份边界全靠它。容器默认 UTC，会和国内时间差 8 小时，
# 典型症状是"刚登录的 token 就显示过期"。
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# tzdata   —— 让 TZ=Asia/Shanghai 真正生效（slim 镜像不带时区库）
# libgomp1 —— onnxruntime（chromadb 的依赖）运行时需要这个共享库，
#             不装会在 import 时报 "libgomp.so.1: cannot open shared object file"
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata libgomp1 \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── 先装依赖，再拷代码 ──
# 这样只要 requirements.txt 没变，改业务代码时这层能直接命中缓存，重建很快。
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── 再拷代码 ──
COPY . .

EXPOSE 8000

# 健康检查用 Python 自带的 urllib，避免为 curl 多装一个包
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else 1)"

# 注意：这里用 0.0.0.0 而不是 127.0.0.1，否则容器外访问不到
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── 如果某个依赖需要现场编译（报 gcc/cc not found）──
# 在 FROM 之后、pip install 之前加上：
#     RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
#         && rm -rf /var/lib/apt/lists/*
# 目前 requirements.txt 里的包在 cp313 上都有预编译 wheel，正常情况下不需要。
