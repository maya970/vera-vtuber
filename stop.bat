@echo off
chcp 65001 >nul
echo [*] 停止占用 8765 端口的薇拉服务 …
set found=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F >nul 2>nul
  set found=1
)
if "%found%"=="0" (echo [i] 没有在运行的服务) else (echo [+] 已停止)
timeout /t 2 >nul
