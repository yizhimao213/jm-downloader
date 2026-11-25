/**
 * 禁漫采集台 · Cloudflare Worker 免费版
 *
 * 设计原则：Worker 只做「转发字节」，所有解密、解析、图片重排都在浏览器完成。
 * 这样单次请求的 CPU 消耗接近 0，不会触发免费档 10ms 限制。
 *
 * 路由：
 *   /jm/api/*     → 转发禁漫移动端接口，附带 token/tokenparam/cookie
 *   /jm/scramble  → 取章节的 scramble_id（HTML 接口，单独密钥）
 *   /jm/img?u=    → 转发图床二进制，附带 Referer
 *   /media/*      → 反代影像馆后台 API（浏览器直连会被 CORS 拦）
 *   其余          → 静态资源（public/）
 */

const JM = {
  apiDomains: ['www.cdngwc.cc', 'www.cdngwc.net', 'www.cdngwc.club', 'www.cdnhjk.net'],
  secret: '185Hcomic3PAPP7R',
  secret2: '18comicAPPContent',
  version: '2.1.13',
  ua: 'Mozilla/5.0 (Linux; Android 9; V1938CT Build/PQ3A.190705.11211812; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Safari/537.36',
};

// 允许代理的图床域名后缀，防止被当成开放代理滥用
const IMG_HOST_SUFFIX = [
  'jmapiproxy1.cc', 'jmapiproxy2.cc', 'jmapinodeudzn.net',
  'cdngwc.cc', 'cdngwc.net', 'cdngwc.club', 'cdnhjk.net',
  '18comic.org', '18comic.vip', '18comic.ink', 'jmcomic.me',
];

// 图床节点优先级：jmapinodeudzn 对 Cloudflare 出口更友好，其余作为备选
const IMG_TRY_ORDER = [
  'cdn-msp.jmapinodeudzn.net', 'cdn-msp3.jmapinodeudzn.net',
  'cdn-msp.jmapiproxy1.cc', 'cdn-msp.jmapiproxy2.cc',
  'cdn-msp2.jmapiproxy2.cc', 'cdn-msp3.jmapiproxy2.cc',
];

// ---------------- MD5（禁漫 token 用，Web Crypto 不支持 MD5） ----------------
function md5hex(str) {
  const bytes = new TextEncoder().encode(str);
  const len = bytes.length;
  const bitLen = len * 8;
  const total = (((len + 8) >> 6) + 1) * 64;
  const buf = new Uint8Array(total);
  buf.set(bytes);
  buf[len] = 0x80;
  const dv = new DataView(buf.buffer);
  dv.setUint32(total - 8, bitLen >>> 0, true);
  dv.setUint32(total - 4, Math.floor(bitLen / 4294967296), true);

  const S = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
  ];
  const K = new Uint32Array(64);
  for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296) >>> 0;

  let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
  const M = new Uint32Array(16);
  const rl = (x, c) => ((x << c) | (x >>> (32 - c))) >>> 0;

  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) M[i] = dv.getUint32(off + i * 4, true);
    let A = a0, B = b0, C = c0, D = d0;
    for (let i = 0; i < 64; i++) {
      let F, g;
      if (i < 16) { F = (B & C) | (~B & D); g = i; }
      else if (i < 32) { F = (D & B) | (~D & C); g = (5 * i + 1) % 16; }
      else if (i < 48) { F = B ^ C ^ D; g = (3 * i + 5) % 16; }
      else { F = C ^ (B | ~D); g = (7 * i) % 16; }
      F = (F + A + K[i] + M[g]) >>> 0;
      A = D; D = C; C = B;
      B = (B + rl(F, S[i])) >>> 0;
    }
    a0 = (a0 + A) >>> 0; b0 = (b0 + B) >>> 0;
    c0 = (c0 + C) >>> 0; d0 = (d0 + D) >>> 0;
  }

  const out = new DataView(new ArrayBuffer(16));
  out.setUint32(0, a0, true);
  out.setUint32(4, b0, true);
  out.setUint32(8, c0, true);
  out.setUint32(12, d0, true);
  let hex = '';
  for (let i = 0; i < 16; i++) hex += out.getUint8(i).toString(16).padStart(2, '0');
  return hex;
}

// ---------------- 禁漫出站 ----------------
let COOKIE = '';        // 同一 isolate 内复用，禁漫移动端要求带 cookie
let DOMAIN_IDX = 0;

async function jmFetch(path, secret) {
  const ts = Math.floor(Date.now() / 1000);
  const headers = {
    'user-agent': JM.ua,
    'token': md5hex(ts + (secret || JM.secret)),
    'tokenparam': `${ts},${JM.version}`,
  };
  if (COOKIE) headers['cookie'] = COOKIE;

  let lastErr = null;
  for (let i = 0; i < JM.apiDomains.length; i++) {
    const idx = (DOMAIN_IDX + i) % JM.apiDomains.length;
    const domain = JM.apiDomains[idx];
    try {
      const resp = await fetch(`https://${domain}${path}`, { headers, redirect: 'follow' });
      if (resp.ok) {
        DOMAIN_IDX = idx;
        return { resp, domain, ts };
      }
      lastErr = new Error(`HTTP ${resp.status} @ ${domain}`);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error('禁漫接口全部域名失败');
}

async function ensureCookie() {
  if (COOKIE) return;
  try {
    const { resp } = await jmFetch('/setting');
    const list = typeof resp.headers.getSetCookie === 'function'
      ? resp.headers.getSetCookie()
      : [resp.headers.get('set-cookie') || ''];
    const pairs = list.filter(Boolean).map((c) => c.split(';')[0].trim()).filter(Boolean);
    if (pairs.length) COOKIE = pairs.join('; ');
  } catch (e) {
    // 拿不到 cookie 不阻断，多数接口仍可用
  }
}

// ---------------- CORS ----------------
function corsHeaders() {
  return {
    'access-control-allow-origin': '*',
    'access-control-allow-methods': 'GET,POST,PUT,DELETE,OPTIONS',
    'access-control-allow-headers': '*',
    'access-control-expose-headers': 'x-jm-ts,x-jm-key,x-jm-domain',
    'access-control-max-age': '86400',
  };
}

function jsonResp(obj, status = 200, extra) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', ...corsHeaders(), ...(extra || {}) },
  });
}

// ---------------- 禁漫接口 ----------------
async function handleJmApi(url) {
  await ensureCookie();
  // /jm/api/search?x=1  ->  /search?x=1
  const sub = url.pathname.replace(/^\/jm\/api/, '') || '/';
  const { resp, domain, ts } = await jmFetch(sub + url.search);
  const body = await resp.text();
  return new Response(body, {
    status: 200,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'x-jm-ts': String(ts),
      'x-jm-key': md5hex(ts + JM.secret),
      'x-jm-domain': domain,
      ...corsHeaders(),
    },
  });
}

async function handleScramble(url) {
  const id = url.searchParams.get('id') || '';
  if (!id) return jsonResp({ error: '缺少 id' }, 400);
  const ts = Math.floor(Date.now() / 1000);
  const qs = new URLSearchParams({
    id,
    mode: 'vertical',
    page: '0',
    app_img_shunt: '1',
    express: 'off',
    v: String(ts),
  });
  // 这个接口用第二把密钥，用错返回 403
  const { resp } = await jmFetch(`/chapter_view_template?${qs}`, JM.secret2);
  const html = await resp.text();
  const m = html.match(/var\s+scramble_id\s*=\s*(\d+)/);
  if (!m) return jsonResp({ id, scramble_id: '220980', fallback: true });
  return jsonResp({ id, scramble_id: m[1] });
}

function hostAllowed(host) {
  return IMG_HOST_SUFFIX.some((s) => host === s || host.endsWith('.' + s));
}

async function handleImg(url) {
  const raw = url.searchParams.get('u') || '';
  if (!raw) return jsonResp({ error: '缺少 u' }, 400);

  let target;
  try {
    target = new URL(raw);
  } catch (e) {
    return jsonResp({ error: 'u 不是合法 URL' }, 400);
  }
  if (target.protocol !== 'https:' || !hostAllowed(target.hostname)) {
    return jsonResp({ error: '域名不在白名单：' + target.hostname }, 403);
  }

  // 原域优先，失败按优先级换节点（不同节点对 Cloudflare 出口的封锁程度不同）
  const candidates = [target.hostname].concat(IMG_TRY_ORDER.filter((h) => h !== target.hostname));
  const headers = {
    'user-agent': JM.ua,
    'accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'x-requested-with': 'com.JMComic3.app',
    'referer': 'https://' + JM.apiDomains[0] + '/',
    'accept-language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    ...(COOKIE ? { cookie: COOKIE } : {}),
  };

  let lastStatus = 0;
  for (const host of candidates) {
    try {
      const resp = await fetch(`https://${host}${target.pathname}${target.search}`, { headers });
      if (resp.ok) {
        return new Response(resp.body, {
          status: 200,
          headers: {
            'content-type': resp.headers.get('content-type') || 'image/jpeg',
            'cache-control': 'public, max-age=86400',
            ...corsHeaders(),
          },
        });
      }
      lastStatus = resp.status;
    } catch (e) {
      lastStatus = lastStatus || 599;
    }
  }

  return jsonResp({ error: '图床全部节点失败，最后状态 ' + lastStatus }, 502);
}

// ---------------- 影像馆反代 ----------------
async function handleMedia(request, url, env) {
  const base = (env.MEDIA_API || '').replace(/\/+$/, '');
  if (!base) return jsonResp({ error: '还没配置 MEDIA_API' }, 500);

  const target = base + url.pathname.replace(/^\/media/, '') + url.search;
  const headers = new Headers();
  for (const [k, v] of request.headers) {
    const lk = k.toLowerCase();
    if (['host', 'cf-connecting-ip', 'cf-ray', 'cf-ipcountry', 'x-forwarded-for', 'x-real-ip'].includes(lk)) continue;
    headers.set(k, v);
  }
  // 影像馆按 Origin/Referer 校验，必须伪装成后台页面
  const site = (env.MEDIA_SITE || '').replace(/\/+$/, '');
  if (site) {
    headers.set('origin', site);
    headers.set('referer', site + '/');
  }

  const init = { method: request.method, headers, redirect: 'follow' };
  if (!['GET', 'HEAD'].includes(request.method)) init.body = await request.arrayBuffer();

  const resp = await fetch(target, init);
  const out = new Headers();
  const ct = resp.headers.get('content-type');
  if (ct) out.set('content-type', ct);
  const cc = resp.headers.get('content-disposition');
  if (cc) out.set('content-disposition', cc);
  for (const [k, v] of Object.entries(corsHeaders())) out.set(k, v);
  return new Response(resp.body, { status: resp.status, headers: out });
}

// ---------------- 入口 ----------------
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    try {
      if (url.pathname.startsWith('/jm/api/')) return await handleJmApi(url);
      if (url.pathname === '/jm/scramble') return await handleScramble(url);
      if (url.pathname === '/jm/img') return await handleImg(url);
      if (url.pathname === '/jm/ping') return jsonResp({ ok: true, domains: JM.apiDomains.length });
      if (url.pathname.startsWith('/media/')) return await handleMedia(request, url, env);
      if (url.pathname === '/api/config') {
        return jsonResp({
          media_api: env.MEDIA_API || '',
          media_site: env.MEDIA_SITE || '',
          has_media_password: !!env.ADMIN_PASSWORD,
          download_thread: 6,
          create_album: env.CREATE_ALBUM !== 'false',
        });
      }
      if (url.pathname === '/api/ping') return jsonResp('ok');
    } catch (e) {
      return jsonResp({ error: String((e && e.message) || e) }, 502);
    }

    return env.ASSETS.fetch(request);
  },
};
