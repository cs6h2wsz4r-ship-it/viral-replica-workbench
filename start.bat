@echo off
cd /d "%~dp0"
set PORT=8765
if not exist "runtime\INSTALLED.txt" (
  echo First run: installing Python, packages, FFmpeg and models into runtime\ ^(about 10 minutes, once^)
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
  if not exist "runtime\INSTALLED.txt" (
    echo Install did not finish. Double-click start.bat again when the network is good.
    pause
    exit /b 1
  )
)
chcp 65001 >nul
rem Parse the whole launch block before the updater can replace this batch file.
(
rem stop an old workbench that still listens on the port
if exist "runtime\server.pid" for /f "usebackq delims=" %%a in ("runtime\server.pid") do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%PORT% ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
echo 正在检查更新…
"runtime\venv\Scripts\python.exe" -X utf8 updater.py --auto
set "PATH=%~dp0runtime\ffmpeg;%PATH%"
start "" http://127.0.0.1:%PORT%
"runtime\venv\Scripts\python.exe" server.py
pause
exit /b 0
)
