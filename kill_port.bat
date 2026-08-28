@echo off
chcp 65001 >nul
:: 结束占用 CVPolishing 服务端口（默认 5090）的进程
set PORT=5090

echo 正在查找占用端口 %PORT% 的进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    set PID=%%a
)

if not defined PID (
    echo 端口 %PORT% 未被占用，无需清理。
    goto :end
)

echo 发现监听进程 PID=%PID%，正在结束...
taskkill /f /pid %PID% >nul 2>&1
if %errorlevel%==0 (
    echo 已结束端口 %PORT% 的进程。
) else (
    echo 结束进程失败，请手动检查。
)

:end
pause
