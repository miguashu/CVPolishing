# CVPolishing 运行镜像
FROM python:3.11-slim

# 系统依赖（pymysql 纯 Python，无需编译；保留 ca-certificates 便于联网）
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 明示端口
EXPOSE 5090

# 用 gunicorn 托管（多 worker，生产可用）；默认从 .env 读取配置
CMD ["sh", "-c", "python init_db.py && gunicorn -w 4 -b 0.0.0.0:${PORT:-5090} app:app"]
