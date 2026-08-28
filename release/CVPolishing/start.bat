@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo   CVPolishing 一键部署（Windows 本地版）
echo ============================================
echo.

rem ---------- 1. 检查 Python ----------
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Python，请先安装 Python 3.10+：https://www.python.org/downloads/
  echo        安装时勾选 "Add Python to PATH"。
  pause
  exit /b 1
)

rem ---------- 2. 检查 MySQL ----------
where mysql >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 MySQL 客户端（mysql 不在 PATH）。
  echo        请安装 MySQL 5.7/8.0 并加入 PATH，或使用 deploy.bat（Docker 方式，无需本机 MySQL）。
  pause
  exit /b 1
)

rem ---------- 3. .env 配置 ----------
if not exist .env (
  echo 未检测到 .env，已复制 .env.example 为 .env。
  copy .env.example .env >nul
  echo.
  echo ============================================================
  echo   请打开 .env 填入你的配置（尤其 LLM_API_KEY 与 DB_PASSWORD）：
  echo   编辑后保存，再重新运行本脚本。
  echo ============================================================
  pause
  exit /b 0
)

rem ---------- 4. 安装依赖 ----------
echo 安装依赖（已安装会自动跳过）...
python -m pip install -r requirements.txt

rem ---------- 5. 初始化数据库（建库 + 导入演示数据）----------
echo 初始化数据库（建库建表 / 导入演示数据 / 种子账号，已存在则跳过）...
python init_db.py
if not errorlevel 1 (
  rem 首次部署：若存在 db_init.sql 且数据库刚建，导入演示记忆与简历
  if exist db_init.sql (
    echo 检测到演示数据文件 db_init.sql，导入长期记忆与简历样例...
    for /f "tokens=1,2 delims==" %%a in (.env) do (
      if "%%a"=="DB_HOST" set DBH=%%b
      if "%%a"=="DB_PORT" set DBP=%%b
      if "%%a"=="DB_USER" set DBU=%%b
      if "%%a"=="DB_PASSWORD" set DBPW=%%b
      if "%%a"=="DB_NAME" set DBN=%%b
    )
    if not defined DBH set DBH=127.0.0.1
    if not defined DBP set DBP=3306
    if not defined DBU set DBU=root
    if not defined DBPW set DBPW=root
    if not defined DBN set DBN=cvpolishing
    set MYSQL_PWD=%DBPW%
    mysql --host=%DBH% --port=%DBP% --user=%DBU% < db_init.sql
    echo 演示数据导入完成。
  )
)

echo.
echo 启动前请确保：
echo   - .env 已填入 LLM_API_KEY
echo   - MySQL 可连接
echo   - 如需向量召回，本地 Ollama 已运行并拉取 bge-m3：ollama pull bge-m3
echo.
echo 浏览器访问： http://localhost:5090
echo 默认管理员账号： admin / admin123!
echo.
echo 正在启动服务...
python app.py
pause
