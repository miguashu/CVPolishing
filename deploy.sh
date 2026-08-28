#!/usr/bin/env bash
# CVPolishing 一键部署脚本（Docker Compose）
set -e

cd "$(dirname "$0")"

echo "============================================"
echo "  CVPolishing 一键启动（Docker 方式）"
echo "============================================"

if ! command -v docker >/dev/null 2>&1; then
  echo "[错误] 未检测到 Docker，请先安装：https://www.docker.com/products/docker-desktop/"
  exit 1
fi

if [ ! -f .env ]; then
  echo "未检测到 .env，已复制 .env.example 为 .env（请按需编辑后再重启）"
  cp .env.example .env
fi

echo "构建并启动服务（应用 + MySQL）..."
docker compose up -d --build

echo
echo "启动完成！浏览器访问： http://localhost:5090"
echo "默认管理员账号： admin / admin123!"
echo "查看日志： docker compose logs -f app"
echo "停止服务： docker compose down"
