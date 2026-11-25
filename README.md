# 禁漫采集台

自托管的禁漫天堂（JM）采集后台：搜索 / 排行 / 作者页 → 批量下载 → 自动解密 → 上传影像馆建相册。
后端 Python 标准库 HTTP 服务 + jmcomic，前端单页（Alpine + Tailwind）。

## 功能

- 关键词检索、JM 号直查、热门排行、作者作品
- 批量选择 → 后台任务下载 → 自动解密 → 上传影像馆建相册
- 任务进度、失败重试（每张 3 轮）、日志逐条可见
- 配置可在网页「设置」修改并落盘（密码不回显）

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

免费档特性：512MB / 0.1 CPU、750 实例小时每月、15 分钟无请求休眠、冷启动约 47 秒、免费自定义域名。

## 环境变量覆盖

`PORT` `MEDIA_API` `MEDIA_SITE` `ADMIN_PASSWORD` `TARGET_FOLDER` `CREATE_ALBUM`
`JM_PROXY` `JM_IMAGE_SUFFIX` `JM_CLIENT_IMPL` `DOWNLOAD_THREAD`

环境变量优先级高于容器内 `config.json`；`config.json` 含凭据，不随仓库分发。
