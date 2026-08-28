@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo   CVPolishing 一键启动（Docker 方式）
echo ============================================

where docker >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Docker，请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/
  pause
  exit /b 1
)

if not exist .env (
  echo 未检测到 .env，已复制 .env.example 为 .env（请按需编辑后再重启）
  copy .env.example .env
)

echo 构建并启动服务（应用 + MySQL）...
docker compose up -d --build

echo.
echo 等待服务就绪...
timeout /t 8 >nul

echo.
echo 启动完成！浏览器访问： http://localhost:5090
echo 默认管理员账号： admin / admin123!
echo 查看日志： docker compose logs -f app
echo 停止服务： docker compose down
pause
