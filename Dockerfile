FROM python:3.11-slim

WORKDIR /app

# 安装编译依赖（tqsdk 的 C 扩展需要）
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用 Docker 层缓存）
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再复制代码
COPY backend/ ./backend/
COPY frontend/ ./frontend/

WORKDIR /app/backend

CMD python -m uvicorn main:app --host 0.0.0.0 --port $PORT