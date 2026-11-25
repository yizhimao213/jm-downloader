@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 正在连接 Cloudflare 隧道 jm.ixoxi.cn ...
cloudflared.exe tunnel --no-autoupdate run --token-file .tunnel_token
pause
