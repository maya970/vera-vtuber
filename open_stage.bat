@echo off
chcp 65001 >nul
REM 用无地址栏的全屏窗口打开直播舞台，方便直播姬/OBS 做窗口捕获
set URL=http://127.0.0.1:8765/stage

set EDGE="C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist %EDGE% set EDGE="C:\Program Files\Microsoft\Edge\Application\msedge.exe"
if exist %EDGE% (
  start "" %EDGE% --app=%URL% --window-size=1920,1080 --start-fullscreen --autoplay-policy=no-user-gesture-required
  exit /b 0
)

set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if exist %CHROME% (
  start "" %CHROME% --app=%URL% --window-size=1920,1080 --start-fullscreen --autoplay-policy=no-user-gesture-required
  exit /b 0
)

echo [x] 没找到 Edge 或 Chrome，直接用默认浏览器打开
start "" %URL%
