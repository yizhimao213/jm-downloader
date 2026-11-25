@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"
echo [1/2] 启动下载器服务 (localhost:8899) ...
start "JM下载器" cmd /k ""%PY%" server.py"
timeout /t 2 >nul
echo [2/2] 连接 Cloudflare 隧道 jm.ixoxi.cn ...
start "JM隧道" cmd /k "cloudflared.exe tunnel --no-autoupdate run --token-file .tunnel_token"
echo.
echo 两个窗口已启动。浏览器打开 https://jm.ixoxi.cn 验证。
timeout /t 5 >nul
