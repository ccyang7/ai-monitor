FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# slim 镜像没有系统时区库，ZoneInfo("Asia/Shanghai") 会抛异常让后端启动失败；
# ca-certificates 保证调用 DeepSeek 等 HTTPS 接口时能校验证书
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -i "$UV_DEFAULT_INDEX" uv

WORKDIR /app

# 先只拷依赖清单：改业务代码时这一层仍然命中缓存，不必重装 LangChain 全家桶
COPY ai-monitor-backend/pyproject.toml ai-monitor-backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY ai-monitor-backend/app ./app
COPY ai-monitor-backend/main.py ./
COPY ai-monitor-backend/scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8001

# 2G 内存只开 1 个 worker：LangChain + 工具链的常驻内存不小，多开一个就多一份
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
