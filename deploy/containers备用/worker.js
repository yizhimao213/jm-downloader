// Cloudflare Worker：jm.ixoxi.cn 入口，把请求转发到 Python 容器
// 容器跑禁漫下载器（jmcomic），通过 Service Binding/Durable Object 路由

const PORT = 8080;
const INACTIVITY_MS = 30 * 60 * 1000; // 空闲 30 分钟后停容器，省钱

export class JmDownloader extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.starting = undefined;
    if (ctx.container?.running) {
      void ctx.blockConcurrencyWhile(() =>
        ctx.container.setInactivityTimeout(INACTIVITY_MS)
      );
    }
  }

  async fetch(request) {
    this.starting ??= this.startAndWait().finally(() => { this.starting = undefined; });
    await this.starting;
    const url = new URL(request.url);
    url.protocol = 'http:';
    url.host = 'container';
    const fwd = new Request(url, request);
    fwd.headers.delete('host');
    return this.ctx.container.getTcpPort(PORT).fetch(fwd);
  }

  async startAndWait() {
    const c = this.ctx.container;
    if (!c.running) {
      c.start({
        image: c.images.base,
        instance: 'lite',
        enableInternet: true, // jmcomic 必须出网访问禁漫 API / 图床
        env: {
          PORT: String(PORT),
          MEDIA_API: this.env.MEDIA_API || 'https://media-api.ixoxi.cn',
          MEDIA_SITE: this.env.MEDIA_SITE || 'https://media.ixoxi.cn',
          ADMIN_PASSWORD: this.env.ADMIN_PASSWORD || '',
          TARGET_FOLDER: this.env.TARGET_FOLDER || '',
          // 三源配置：容器在 Cloudflare 边缘出网，source_proxy 必须留空
          SOURCE_PROXY: '',
          PICA_ACCOUNT: this.env.PICA_ACCOUNT || '',
          PICA_PASSWORD: this.env.PICA_PASSWORD || '',
        },
      });
    }
    await c.setInactivityTimeout(INACTIVITY_MS);
    const port = c.getTcpPort(PORT);
    for (let i = 0; i < 100; i++) {
      try {
        const r = await port.fetch('http://container/api/ping', { signal: AbortSignal.timeout(1000) });
        await r.body?.cancel();
        if (r.ok) return;
      } catch { /* 还没起来，继续探活 */ }
      await scheduler.wait(200);
    }
    throw new Error('容器 8080 端口未就绪');
  }
}

export default {
  async fetch(request, env) {
    // 所有请求（含首页、/api/*、封面）都进同一个容器实例
    return env.JM_CONTAINER.getByName('jm-main').fetch(request);
  },
};
