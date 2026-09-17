# AgentFleet 后端 — FastAPI + SQLite（Render / Railway 通用）
# 构建: docker build -t agentfleet-api .
# 运行: docker run -p 8765:8765 -e DATABASE_URL=sqlite:////data/agentfleet.db agentfleet-api
FROM python:3.12-slim

WORKDIR /app

# 先装依赖，利用 Docker 层缓存；全部依赖均有 manylinux wheel，无需 gcc
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 只复制后端源码：backend 只依赖 backend.* + 标准库 + 上述 pip 包（见 docs/deploy.md）
COPY backend ./backend

# SQLite 数据目录：
#   Render 免费层是临时盘（服务重启数据还在、重新部署会清空）；
#   要长期留数据，在 Render 加一个 Persistent Disk 挂载到 /data 即可，无需改代码。
RUN mkdir -p /data
ENV DATABASE_URL=sqlite:////data/agentfleet.db \
    PYTHONUNBUFFERED=1

EXPOSE 8765

# Render 会通过 $PORT 注入端口；本地/其它平台默认 8765
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8765}"]
