# 禁漫下载器 → Cloudflare Containers 部署包

把 `jm.ixoxi.cn` 指向部署在 Cloudflare 边缘的 Python 容器（jmcomic）。

## 前置条件（必须）

1. **Cloudflare 账号升级到 Workers Paid**（$5/月）——免费版无法使用 Containers。
   升级地址：https://dash.cloudflare.com/?to=/:account/workers/plans
2. **本机安装 Docker Desktop 并启动**（`docker info` 能跑通）。
   Wrangler 用本机 Docker 构建并推送镜像。

## 部署步骤

在本目录（`jm-downloader/deploy`）执行：

```bash
# 1. 设置线上后台密码为 Worker Secret（不会写进代码/配置）
npx wrangler secret put ADMIN_PASSWORD
# 提示时粘贴影像馆线上后台密码，回车

# 2. 可选：设置其他 secret（不设则用代码默认值）
# npx wrangler secret put MEDIA_API      # 默认 https://media-api.ixoxi.cn
# npx wrangler secret put MEDIA_SITE     # 默认 https://media.ixoxi.cn
# npx wrangler secret put TARGET_FOLDER  # 默认空（直接用漫画名做文件夹）

# 3. 部署（构建镜像 + 推送 + 部署 Worker + 绑定域名）
npx wrangler deploy
```

`wrangler.jsonc` 已配好 `routes: jm.ixoxi.cn`，部署会自动创建 DNS 记录。

首次部署后等几分钟让 Cloudflare  provision 容器，然后访问 https://jm.ixoxi.cn 验证。

## 验证

```bash
curl https://jm.ixoxi.cn/api/ping      # 应返回 {"ok":true,...}
```

网页右上角「设置」里确认 API 指向线上、文件夹前缀为空，即可搜索下载。

## 文件说明

- `Dockerfile`（项目根目录）— 容器镜像定义，python:3.13-slim + jmcomic 依赖
- `requirements.txt` — Python 依赖固定版本
- `worker.js` — Worker 入口，把 jm.ixoxi.cn 请求转发到容器，注入密码/配置
- `wrangler.jsonc` — 容器 + Worker + 域名路由配置（已填 zone_id）

## 重要设计点

- **容器出网**：`enableInternet: true`（jmcomic 需访问禁漫 API 与图床），已验证无本机代理时仍可用
- **密码**：作为 Worker Secret 传给容器 env，不落盘；网页「设置」里也可临时改
- **空闲回收**：容器 30 分钟无请求自动停止，省钱；下次访问冷启动约数秒
- **前台不展示**：item 写 `hidden=true, inGallery=false`，相册 `hidden=true`（沿用现有逻辑）
