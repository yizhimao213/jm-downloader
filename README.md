# 禁漫采集台（多源）

一个自托管的漫画采集后台，支持三个数据源：禁漫天堂（jm）、绅士漫画（wnacg）、哔咔漫画（pica）。
后端 Python 标准库 HTTP 服务 + jmcomic，前端单页（Alpine + Tailwind）。

## 功能

- 三源搜索 / 排行 / 作者页，按源切换筛选条件
- 批量选择 → 后台任务下载 → 自动解密 → 上传影像馆建相册
- 任务进度、失败重试（每张 3 轮）、日志逐条可见
- 配置可通过网页「设置」修改并落盘（凭据不回显）

## 本地运行

```bash
pip install -r deploy/requirements.txt
python server.py           # 默认 http://localhost:8899
```

## Docker

```bash
docker build -t jm-downloader .
docker run -p 8899:8080 --env-file .env jm-downloader
```

## 部署（Render 免费档）

仓库已带 `render.yaml`。在 Render 新建 Blueprint 并连本仓库，环境变量按需填：

| 变量 | 说明 |
|---|---|
| `MEDIA_API` | 影像馆后端 API，如 `https://media-api.example.com` |
| `MEDIA_SITE` | 影像馆前台地址 |
| `ADMIN_PASSWORD` | 影像馆后台密码 |
| `TARGET_FOLDER` | 相册文件夹前缀，留空=用漫画名 |
| `DOWNLOAD_THREAD` | 图片并发数，免费档建议 6 |
| `PICA_ACCOUNT` / `PICA_PASSWORD` | 哔咔账号，留空则仅 jm + wnacg 可用 |
| `SOURCE_PROXY` | wnacg/pica 代理，容器部署必须留空（境外出网直连） |

## 部署（Cloudflare Containers）

需 Workers Paid（$5/月），免费版不支持 Containers。
配置见 `deploy/containers备用/`，容器 `enableInternet: true` 提供境外出网。

## 环境变量覆盖

`PORT` `MEDIA_API` `MEDIA_SITE` `ADMIN_PASSWORD` `TARGET_FOLDER` `CREATE_ALBUM`
`JM_PROXY` `SOURCE_PROXY` `PICA_ACCOUNT` `PICA_PASSWORD` `DOWNLOAD_THREAD`

环境变量优先级高于容器内 `config.json`；`config.json` 不随仓库分发。
