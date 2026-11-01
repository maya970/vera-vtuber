@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 薇拉 · 书库直播间

echo ==========================================
echo   虚拟主播 · 开盒即用
echo   没有 Python 也会自动准备运行环境
echo ==========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ensure_runtime.ps1"
if errorlevel 1 (
  echo [x] 运行环境准备失败
  pause
  exit /b 1
)

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" set "PY=%~dp0runtime\python.cmd"
if not exist "%PY%" set "PY=python"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=studio"

echo [*] 使用 %PY%
echo [*] 启动服务 http://127.0.0.1:8765

if /I "%MODE%"=="stage" (
  start "" "%~dp0open_stage.bat"
) else if /I "%MODE%"=="panel" (
  start "" http://127.0.0.1:8765/panel
) else (
  start "" http://127.0.0.1:8765/
)

"%PY%" run.py

echo.
echo [!] 服务已退出
pause
