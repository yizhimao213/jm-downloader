# 禁漫采集台

搜索 / 排行 / 作者页 → 批量采集 → 自动解密 → 上传影像馆建相册。

线上版本跑在 **Cloudflare Workers 免费档**，本机不需要运行任何常驻进程。

## 线上架构

```
浏览器（你的电脑）
 ├─ 搜索/详情：Worker 转发禁漫接口 → 浏览器用 AES-256-ECB 解密 JSON
 ├─ 图片：Worker 转发图床字节 → canvas 逐段重排还原
 └─ 上传：Worker 反代影像馆 API → 建相册、写 items
Cloudflare Worker（免费档）
 └─ 只做字节转发，不做解密 → 单次请求 CPU 接近 0，不触发 10ms 限制
```

这样安排的原因：免费 Worker 每次请求只有 10ms CPU，Python 版解密一张图就要 200ms 以上；把解密挪到浏览器后，Worker 只剩转发，免费档可以长期在线、无休眠、无额度限制。

## 目录

| 路径 | 说明 |
|---|---|
| `cf/worker.js` | Worker：禁漫接口转发、图床代理（多节点自动切换）、影像馆反代 |
| `cf/public/index.html` | 前端页面 |
| `cf/public/assets/jmclient.js` | 浏览器数据层：MD5、AES-256-ECB、图片重排、上传 |
| `cf/wrangler.toml` | 部署配置（自定义域名 `jm.ixoxi.cn`） |
| `server.py` / `Dockerfile` / `render.yaml` | 早期 Python 版，保留作本地开发参考，线上不使用 |

## 部署

```bash
cd cf
export CLOUDFLARE_API_TOKEN=...      # 需要 Workers Scripts:Edit、Workers Routes:Edit、DNS:Edit、Zone:Read
export CLOUDFLARE_ACCOUNT_ID=...
npx wrangler deploy
```

`wrangler.toml` 里已声明 `routes = [{ pattern = "jm.ixoxi.cn", custom_domain = true }]`，部署时会自动创建 CNAME 与证书。同名 DNS 记录必须事先删除，否则报 `code 100117`。

## 配置

影像馆地址写在 `wrangler.toml` 的 `[vars]`：

| 变量 | 说明 |
|---|---|
| `MEDIA_API` | 影像馆后端 API |
| `MEDIA_SITE` | 影像馆前台地址 |
| `CREATE_ALBUM` | 是否自动建相册 |

影像馆后台密码与并存数量存在浏览器 `localStorage`，在网页右上角「设置」里填写，不经过服务端也不入仓库。

## 技术要点

- **token**：`md5(ts + "185Hcomic3PAPP7R")`，`tokenparam = "{ts},2.1.13"`
- **响应解密**：base64 → AES-256-ECB（密钥为上述 md5 的 32 个 ASCII 字符）→ 去 PKCS7 填充
- **图片分割数**：`aid < scramble_id` 为 0；`< 268850` 为 10；否则 `md5(aid+filename)` 末位 `% (aid<421926?10:8) * 2 + 2`
- **图片重排**：高度按 num 等分，从底部往上取段贴到顶部，余数并入首段
- **图床**：`jmapinodeudzn.net` 系列对 Cloudflare 出口可用，`jmapiproxy*.cc` 会被 403，Worker 按顺序自动换节点
