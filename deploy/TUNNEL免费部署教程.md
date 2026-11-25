# 禁漫下载器 → jm.ixoxi.cn（已部署完成，免费方案）

## 当前状态（2026-11-24 已验证）

站点已上线：**https://jm.ixoxi.cn**
- 首页 HTTP 200，`/api/ping` 返回 `{"ok":true}`，关键词搜索实测 4147 条结果
- 方案：Cloudflare Tunnel（免费版账号可用），源站是本机 `localhost:8899`
- 隧道 ID：`0d264ef0-1c57-4926-8004-458c062776b3`
- DNS：`jm.ixoxi.cn` CNAME → `<隧道ID>.cfargotunnel.com`（已代理）
- cloudflared 二进制：`jm-downloader/cloudflared.exe`（2026.11.2，53MB）
- 隧道 token：`jm-downloader/.tunnel_token`（敏感，勿外传；泄露可撤销重建）

> 代价：这台电脑必须开机联网。关机后站点暂停，开机重新启动服务即可恢复。

## 日常启动（每次开机后）

双击 `deploy\启动全部.bat`，它会自动：
1. 开一个窗口跑 `python server.py`（下载器，8899）
2. 开一个窗口跑 `cloudflared.exe tunnel run --token ...`（隧道）

看到隧道窗口出现 `Registered tunnel connection` 即连上。

只重启隧道不重启下载器：双击 `deploy\启动隧道.bat`。

## 开机自启（可选）

隧道装成 Windows 服务（装完开机自动连，无需双击）：

```powershell
cd "D:\claude工作文件夹\blog\jm-downloader"
set /p TOKEN=<.tunnel_token
cloudflared.exe tunnel --no-autoupdate service install
```

> 注意：`service install` 可能需要管理员权限的 PowerShell。
> 下载器 `python server.py` 也要自启：Win+R 输入 `shell:startup`，把 python 快捷方式放进去。

## 验证

```powershell
curl https://jm.ixoxi.cn/api/ping      # {"ok":true,...}
```

网页右上角「设置」确认：
- API 地址：`https://media-api.ixoxi.cn`
- 文件夹前缀：留空（纯漫画名做文件夹）
- 填一次线上后台密码（只存内存，重启失效）

## 这次部署是怎么做的（备查）

用 Cloudflare API + 本机代理下载，全程没用浏览器授权：

1. 本机代理 `127.0.0.1:7897` 下载 cloudflared.exe（GitHub 直链经代理）
2. `POST /accounts/{account}/cfd_tunnel` 创建隧道，响应里拿 token（只返回一次）
3. `PUT /accounts/{account}/cfd_tunnel/{id}/configurations`（注意是复数）配 ingress：
   `jm.ixoxi.cn → http://localhost:8899`
4. `POST /zones/{zone_id}/dns_records` 建 CNAME 到 `{id}.cfargotunnel.com`
5. `cloudflared.exe tunnel run --token <token>` 启动，4 条 QUIC 连接注册成功

服务器要求 Workers Paid 的 Containers 方案没走（账号免费版）；这个是免费版唯一能用的正式域名方案。

## 换域名 / 重建隧道

如果要改域名或隧道坏了：
1. 用 API 删旧隧道、建新隧道拿新 token，存进 `.tunnel_token`
2. 更新 `configurations` 的 ingress hostname
3. 更新 DNS CNAME 的 content 为新隧道 ID

## 文件说明

- `cloudflared.exe` — 隧道客户端（根目录，53MB）
- `.tunnel_token` — 隧道 token（敏感）
- `deploy/启动全部.bat` / `启动隧道.bat` — 一键启动
- `deploy/containers备用/` — 以后升级 Workers Paid 后可走边缘容器（Dockerfile 在 `jm-downloader/Dockerfile`）
