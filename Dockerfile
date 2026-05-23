FROM python:3.12-slim-bookworm

WORKDIR /app

# Python 依赖（asyncpg 为纯 Python 驱动，无需编译工具链）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY . .

# 默认运行 API 服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
