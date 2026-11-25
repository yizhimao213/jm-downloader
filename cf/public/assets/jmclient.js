/**
 * 禁漫采集台 · 浏览器数据层
 *
 * 与旧版 Python 服务端的区别：解密、图片重排、上传全部在浏览器完成。
 * Worker 只转发字节，因此 Cloudflare 免费档可以长期运行。
 */
(function () {
  'use strict';

  // ============ MD5 ============
  function md5hex(str) {
    const bytes = new TextEncoder().encode(str);
    const len = bytes.length, bitLen = len * 8;
    const total = (((len + 8) >> 6) + 1) * 64;
    const buf = new Uint8Array(total);
    buf.set(bytes);
    buf[len] = 0x80;
    const dv = new DataView(buf.buffer);
    dv.setUint32(total - 8, bitLen >>> 0, true);
    dv.setUint32(total - 4, Math.floor(bitLen / 4294967296), true);

    const S = [7,12,17,22,7,12,17,22,7,12,17,22,7,12,17,22,
               5,9,14,20,5,9,14,20,5,9,14,20,5,9,14,20,
               4,11,16,23,4,11,16,23,4,11,16,23,4,11,16,23,
               6,10,15,21,6,10,15,21,6,10,15,21,6,10,15,21];
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
    out.setUint32(0, a0, true); out.setUint32(4, b0, true);
    out.setUint32(8, c0, true); out.setUint32(12, d0, true);
    let hex = '';
    for (let i = 0; i < 16; i++) hex += out.getUint8(i).toString(16).padStart(2, '0');
    return hex;
  }

  // ============ AES-256-ECB 解密（Web Crypto 不支持 ECB，这里纯 JS 实现）============
  const SBOX = new Uint8Array([
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
  ]);
  const INV_SBOX = new Uint8Array(256);
  for (let i = 0; i < 256; i++) INV_SBOX[SBOX[i]] = i;

  function gmul(a, b) {
    let p = 0;
    for (let i = 0; i < 8; i++) {
      if (b & 1) p ^= a;
      const hi = a & 0x80;
      a = (a << 1) & 0xff;
      if (hi) a ^= 0x1b;
      b >>= 1;
    }
    return p;
  }

  function expandKey256(key) {
    const w = new Uint8Array(240);
    w.set(key);
    let rcon = 1;
    for (let i = 32; i < 240; i += 4) {
      let t0 = w[i - 4], t1 = w[i - 3], t2 = w[i - 2], t3 = w[i - 1];
      if (i % 32 === 0) {
        const tmp = t0;
        t0 = SBOX[t1] ^ rcon; t1 = SBOX[t2]; t2 = SBOX[t3]; t3 = SBOX[tmp];
        rcon = ((rcon << 1) ^ ((rcon & 0x80) ? 0x1b : 0)) & 0xff;
      } else if (i % 32 === 16) {
        t0 = SBOX[t0]; t1 = SBOX[t1]; t2 = SBOX[t2]; t3 = SBOX[t3];
      }
      w[i] = w[i - 32] ^ t0; w[i + 1] = w[i - 31] ^ t1;
      w[i + 2] = w[i - 30] ^ t2; w[i + 3] = w[i - 29] ^ t3;
    }
    return w;
  }

  function invShiftRows(s) {
    for (let r = 1; r < 4; r++) {
      const a = s[r], b = s[r + 4], c = s[r + 8], d = s[r + 12];
      s[r + 4 * ((0 + r) % 4)] = a;
      s[r + 4 * ((1 + r) % 4)] = b;
      s[r + 4 * ((2 + r) % 4)] = c;
      s[r + 4 * ((3 + r) % 4)] = d;
    }
  }

  function invMixColumns(s) {
    for (let c = 0; c < 4; c++) {
      const i = c * 4;
      const a0 = s[i], a1 = s[i + 1], a2 = s[i + 2], a3 = s[i + 3];
      s[i]     = gmul(a0,14) ^ gmul(a1,11) ^ gmul(a2,13) ^ gmul(a3,9);
      s[i + 1] = gmul(a0,9)  ^ gmul(a1,14) ^ gmul(a2,11) ^ gmul(a3,13);
      s[i + 2] = gmul(a0,13) ^ gmul(a1,9)  ^ gmul(a2,14) ^ gmul(a3,11);
      s[i + 3] = gmul(a0,11) ^ gmul(a1,13) ^ gmul(a2,9)  ^ gmul(a3,14);
    }
  }

  function aesEcbDecrypt(keyBytes, ct) {
    const w = expandKey256(keyBytes);
    const blocks = Math.floor(ct.length / 16);
    const out = new Uint8Array(blocks * 16);
    const s = new Uint8Array(16);
    for (let n = 0; n < blocks; n++) {
      s.set(ct.subarray(n * 16, n * 16 + 16));
      for (let i = 0; i < 16; i++) s[i] ^= w[14 * 16 + i];
      for (let r = 13; r >= 1; r--) {
        invShiftRows(s);
        for (let i = 0; i < 16; i++) s[i] = INV_SBOX[s[i]];
        for (let i = 0; i < 16; i++) s[i] ^= w[r * 16 + i];
        invMixColumns(s);
      }
      invShiftRows(s);
      for (let i = 0; i < 16; i++) s[i] = INV_SBOX[s[i]];
      for (let i = 0; i < 16; i++) s[i] ^= w[i];
      out.set(s, n * 16);
    }
    return out;
  }

  function b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  /** 解密禁漫接口的 data 字段：base64 -> AES-256-ECB -> 去 PKCS7 填充 */
  function decodePayload(b64, keyHex) {
    const ct = b64ToBytes(b64);
    const key = new TextEncoder().encode(keyHex);
    const pt = aesEcbDecrypt(key, ct);
    let end = pt.length;
    const pad = pt[pt.length - 1];
    if (pad > 0 && pad <= 16 && pad <= pt.length) end = pt.length - pad;
    return new TextDecoder('utf-8').decode(pt.subarray(0, end));
  }

  // ============ 禁漫接口 ============
  const IMG_DOMAINS = [
    'cdn-msp.jmapinodeudzn.net', 'cdn-msp3.jmapinodeudzn.net',
    'cdn-msp.jmapiproxy1.cc', 'cdn-msp.jmapiproxy2.cc',
    'cdn-msp2.jmapiproxy2.cc',
  ];
  let imgDomainIdx = 0;

  async function jmApi(sub) {
    const r = await fetch('/jm/api' + sub);
    const ts = r.headers.get('x-jm-ts') || '';
    const key = r.headers.get('x-jm-key') || '';
    const text = await r.text();
    let outer;
    try { outer = JSON.parse(text); } catch (e) { throw new Error('禁漫返回非 JSON：' + text.slice(0, 120)); }
    if (outer.code && outer.code !== 200) throw new Error('禁漫接口报错 code=' + outer.code);
    if (!outer.data) return outer;
    if (typeof outer.data !== 'object') {
      const plain = decodePayload(outer.data, key);
      return JSON.parse(plain);
    }
    return outer.data;
  }

  function imgProxy(url) {
    return '/jm/img?u=' + encodeURIComponent(url);
  }

  function coverUrl(aid, size) {
    const d = IMG_DOMAINS[imgDomainIdx % IMG_DOMAINS.length];
    return `https://${d}/media/albums/${aid}${size || '_3x4'}.jpg`;
  }

  async function getScrambleId(photoId) {
    const r = await fetch('/jm/scramble?id=' + encodeURIComponent(photoId));
    const d = await r.json();
    return String(d.scramble_id || '220980');
  }

  /** 计算图片分割数，规则来自禁漫客户端 */
  function segNum(photoId, filename, scrambleId) {
    const aid = parseInt(photoId, 10);
    const sid = parseInt(scrambleId, 10) || 220980;
    if (aid < sid) return 0;
    if (aid < 268850) return 10;
    const x = aid < 421926 ? 10 : 8;
    const h = md5hex(String(aid) + filename);
    const last = h.charCodeAt(h.length - 1);
    return (last % x) * 2 + 2;
  }

  // ============ 结果映射 ============
  function splitAuthors(v) {
    if (Array.isArray(v)) return v.map((s) => String(s).trim()).filter(Boolean);
    return String(v || '').split(/[,，、/]/).map((s) => s.trim()).filter(Boolean);
  }

  function mapItem(it) {
    const cat = it.category || {};
    const sub = it.category_sub || {};
    return {
      id: String(it.id),
      title: it.name || '',
      author: splitAuthors(it.author).join(', '),
      authors: splitAuthors(it.author),
      tags: (Array.isArray(it.tags) ? it.tags : []).slice(0, 8),
      pages: 0,
      update: String(it.update_at || ''),
      cover: imgProxy(coverUrl(it.id)),
      categoryId: String(cat.id || ''),
      categoryTitle: String(cat.title || ''),
      subCategoryId: String(sub.id || ''),
      subCategoryTitle: String(sub.title || ''),
    };
  }

  function mapPage(data, page) {
    const list = data.content || data.list || [];
    return { results: list.map(mapItem), total: data.total || list.length, page };
  }

  // ============ 分类常量 ============
  const FILTERS = {
    orders: [
      { value: 'mr', label: '最新' }, { value: 'mv', label: '最多观看' },
      { value: 'tf', label: '最多收藏' }, { value: 'mp', label: '最多图片' },
    ],
    times: [
      { value: 'a', label: '全部' }, { value: 't', label: '今日' },
      { value: 'w', label: '本周' }, { value: 'm', label: '本月' },
    ],
    categories: [
      { value: '0', label: '全部' }, { value: 'doujin', label: '同人' },
      { value: 'single', label: '单本' }, { value: 'short', label: '短篇' },
      { value: 'another', label: '其他' }, { value: 'hanman', label: '韩漫' },
      { value: 'meiman', label: '美漫' }, { value: 'doujin_cosplay', label: 'Cosplay' },
      { value: '3D', label: '3D' }, { value: 'english_site', label: '英文站' },
    ],
    subCategories: {
      '': ['全部'],
      doujin: ['全部', '汉化', '日语'],
      single: ['全部', '汉化', '日语'],
      short: ['全部', '汉化', '日语'],
      another: ['全部', '其他漫画', '3D', 'Cosplay'],
    },
    sourceCategories: {},
  };

  // ============ 配置 ============
  const CFG_KEY = 'jm_cfg_v2';
  const cfg = {
    media_api: 'https://media-api.ixoxi.cn',
    media_site: 'https://media.ixoxi.cn',
    media_password: '',
    target_folder: '',
    create_album: true,
    download_thread: 4,
  };
  try { Object.assign(cfg, JSON.parse(localStorage.getItem(CFG_KEY) || '{}')); } catch (e) { /* ignore */ }
  function saveCfg() {
    const { media_password, media_api, media_site, target_folder, create_album, download_thread } = cfg;
    localStorage.setItem(CFG_KEY, JSON.stringify({ media_api, media_site, media_password, target_folder, create_album, download_thread }));
  }

  // ============ 影像馆 ============
  let mediaToken = '';
  let mediaTokenAt = 0;

  async function mediaLogin(force) {
    if (!force && mediaToken && Date.now() - mediaTokenAt < 6 * 86400 * 1000) return mediaToken;
    if (!cfg.media_password) throw new Error('还没填影像馆后台密码，请在右上角「设置」里填写');
    const r = await fetch('/media/api/admin/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password: cfg.media_password }),
    });
    const text = await r.text();
    let d = {};
    try { d = JSON.parse(text); } catch (e) { throw new Error('影像馆登录返回异常：' + text.slice(0, 120)); }
    if (!r.ok) throw new Error(`影像馆登录失败（${r.status}）：${text.slice(0, 150)}`);
    if (!d.token) throw new Error('影像馆登录没返回 token：' + text.slice(0, 150));
    mediaToken = d.token;
    mediaTokenAt = Date.now();
    return mediaToken;
  }

  async function mediaApi(method, path, opts = {}, retried) {
    const token = await mediaLogin();
    const headers = Object.assign({ authorization: 'Bearer ' + token }, opts.headers || {});
    let body = opts.body;
    if (opts.json !== undefined) {
      headers['content-type'] = 'application/json';
      body = JSON.stringify(opts.json);
    }
    const r = await fetch('/media' + path, { method, headers, body });
    const text = await r.text();
    if (r.status === 401 && !retried) {
      // 只重试一次，避免密码错误时无限递归
      mediaToken = '';
      return mediaApi(method, path, opts, true);
    }
    if (!r.ok) throw new Error(`影像馆 ${method} ${path} 失败（${r.status}）：${text.slice(0, 200)}`);
    if (!text) return {};
    try { return JSON.parse(text); } catch (e) { return { raw: text }; }
  }

  function cleanPart(s, limit) {
    return String(s || '').replace(/[\\/:*?"<>|]/g, '-').replace(/\s+/g, ' ').replace(/^[\s-]+|[\s-]+$/g, '').slice(0, limit || 60);
  }

  function extractAuthor(title) {
    const re = /[\[【]([^\]】]+)[\]】]/g;
    let m;
    while ((m = re.exec(title || ''))) {
      const b = m[1].trim();
      if (!b) continue;
      if (/(DL版|汉化|翻訳|翻译|中文|中国|完结|单行本|全彩|修正|\d+\s*P$)/i.test(b)) continue;
      return b;
    }
    return '';
  }

  function comicName(title, jmId, author) {
    const raw = String(title || '').trim();
    const a = cleanPart(author, 40) || cleanPart(extractAuthor(raw), 40);
    const core = cleanPart(raw.replace(/[\[【][^\]】]*[\]】]/g, ' '), 60);
    const parts = [];
    if (jmId) parts.push('JM' + jmId);
    if (a) parts.push(a);
    if (core) parts.push(core);
    return parts.join(' - ') || ('JM' + jmId);
  }

  function newKey(name) {
    const rid = Math.random().toString(16).slice(2, 12);
    const safe = String(name || 'file').replace(/[^\w.\-一-鿿]/g, '_').slice(0, 80) || 'file';
    const d = new Date();
    return `image/${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${rid}-${safe}`;
  }

  async function uploadImage(blob, filename, title, folder) {
    const key = newKey(filename);
    const qs = new URLSearchParams({ key, name: filename, store: 'r2' });
    const token = await mediaLogin();
    const put = await fetch('/media/api/admin/upload?' + qs.toString(), {
      method: 'PUT',
      headers: { authorization: 'Bearer ' + token, 'content-type': blob.type || 'image/jpeg' },
      body: blob,
    });
    if (!put.ok) throw new Error(`上传失败 ${put.status}：${(await put.text()).slice(0, 150)}`);
    const item = await mediaApi('POST', '/api/admin/items', {
      json: {
        kind: 'image', key, size: blob.size, name: filename,
        title: title || filename, mime: blob.type || 'image/jpeg',
        folder, tags: [], hidden: true, inGallery: false,
      },
    });
    return item.id;
  }

  async function getOrCreateAlbum(title, description) {
    const albums = await mediaApi('GET', '/api/admin/albums');
    const list = Array.isArray(albums) ? albums : (albums.list || []);
    const hit = list.find((a) => a.title === title);
    if (hit) return hit.id;
    const a = await mediaApi('POST', '/api/admin/albums', {
      json: { title, description: description || '', hidden: true },
    });
    return a.id;
  }

  // ============ 图片解密 ============
  function descrambleBitmap(bitmap, num) {
    const w = bitmap.width, h = bitmap.height;
    const canvas = document.createElement('canvas');
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    if (!num) {
      ctx.drawImage(bitmap, 0, 0);
      return canvas;
    }
    const over = h % num;
    const move = Math.floor(h / num);
    for (let i = 0; i < num; i++) {
      let ySrc = h - move * (i + 1) - over;
      let yDst = move * i;
      let m = move;
      if (i === 0) m += over; else yDst += over;
      ctx.drawImage(bitmap, 0, ySrc, w, m, 0, yDst, w, m);
    }
    return canvas;
  }

  function canvasToBlob(canvas, type, quality) {
    return new Promise((resolve) => canvas.toBlob((b) => resolve(b), type || 'image/jpeg', quality || 0.92));
  }

  async function fetchImageBlob(url) {
    const r = await fetch(imgProxy(url));
    if (!r.ok) throw new Error('取图失败 ' + r.status);
    return r.blob();
  }

  async function processImage(url, photoId, filename, scrambleId) {
    const blob = await fetchImageBlob(url);
    // 分割数要用不带扩展名的文件名参与 md5，带后缀会算出错误段数
    const stem = String(filename).replace(/\.[a-z0-9]+$/i, '');
    const num = segNum(photoId, stem, scrambleId);
    if (!num) return blob;
    const bitmap = await createImageBitmap(blob);
    const canvas = descrambleBitmap(bitmap, num);
    if (bitmap.close) bitmap.close();
    const out = await canvasToBlob(canvas, blob.type || 'image/jpeg');
    return out || blob;
  }

  // ============ 任务队列 ============
  const JOBS_KEY = 'jm_jobs_v2';
  const state = { jobs: [], running: false };
  try { state.jobs = JSON.parse(localStorage.getItem(JOBS_KEY) || '[]'); } catch (e) { state.jobs = []; }
  // 刷新后未完成的任务标记为中断，避免永远转圈
  state.jobs.forEach((j) => { if (j.status === 'running' || j.status === 'queued') j.status = 'error', j.error = j.error || '页面刷新导致中断'; });

  function persistJobs() {
    try { localStorage.setItem(JOBS_KEY, JSON.stringify(state.jobs.slice(-40))); } catch (e) { /* ignore */ }
  }

  function newJob(id) {
    const job = {
      id: 'j' + Date.now() + Math.random().toString(16).slice(2, 6),
      jm_id: String(id), title: '', comic_name: '',
      status: 'queued', phase: '排队中', total: 0, done: 0,
      missing: 0, error: '', logs: [],
    };
    state.jobs.push(job);
    persistJobs();
    return job;
  }

  function log(job, msg) {
    job.logs.push(`[${new Date().toLocaleTimeString('zh-CN', { hour12: false })}] ${msg}`);
    if (job.logs.length > 200) job.logs.shift();
    persistJobs();
  }

  // 并发池
  async function pool(items, limit, worker) {
    const queue = items.slice();
    const runners = new Array(Math.min(limit, queue.length)).fill(0).map(async () => {
      while (queue.length) {
        const item = queue.shift();
        await worker(item);
      }
    });
    await Promise.all(runners);
  }

  async function runJob(job) {
    job.status = 'running';
    job.phase = '读取本子信息';
    persistJobs();

    const album = await jmApi('/album?id=' + encodeURIComponent(job.jm_id));
    job.title = album.name || ('JM' + job.jm_id);
    const authors = splitAuthors(album.author);
    job.comic_name = comicName(job.title, job.jm_id, authors.join(' '));
    log(job, `标题：${job.title}`);

    // 章节列表：单章本子的 series 只有自己
    let chapters = Array.isArray(album.series) && album.series.length
      ? album.series.map((s) => ({ id: String(s.id), name: s.name || '' }))
      : [{ id: String(album.id || job.jm_id), name: album.name || '' }];
    log(job, `共 ${chapters.length} 章`);

    const folder = cleanPart(cfg.target_folder || '', 40) || job.comic_name;
    const albumId = cfg.create_album ? await getOrCreateAlbum(job.comic_name, job.title) : null;

    let seq = 0;
    const itemIds = [];
    let failed = 0;

    for (let ci = 0; ci < chapters.length; ci++) {
      const ch = chapters[ci];
      job.phase = `第 ${ci + 1}/${chapters.length} 章：读取图片列表`;
      persistJobs();

      const detail = await jmApi('/chapter?id=' + encodeURIComponent(ch.id));
      let names = detail.page_arr;
      if (typeof names === 'string') { try { names = JSON.parse(names); } catch (e) { names = null; } }
      if (!Array.isArray(names) || !names.length) {
        names = (detail.images || []).map((x) => (typeof x === 'string' ? x : x.name)).filter(Boolean);
      }
      if (!names.length) { log(job, `章节 ${ch.id} 没有图片，跳过`); continue; }

      const domain = detail.data_original_domain || IMG_DOMAINS[imgDomainIdx % IMG_DOMAINS.length];
      const scrambleId = await getScrambleId(ch.id);
      job.total += names.length;
      log(job, `章节 ${ch.id}：${names.length} 张，scramble=${scrambleId}`);

      await pool(names, Math.max(1, Math.min(6, cfg.download_thread || 4)), async (filename) => {
        if (!filename) return;
        const index = ++seq;
        const num = String(index).padStart(5, '0');
        const ext = (filename.match(/\.[a-z0-9]+$/i) || ['.jpg'])[0];
        const display = num + ext;
        const url = `https://${domain}/media/photos/${ch.id}/${filename}`;
        try {
          const blob = await processImage(url, ch.id, filename, scrambleId);
          const itemId = await uploadImage(blob, display, job.comic_name, folder);
          if (itemId) itemIds.push(itemId);
          job.done += 1;
          if (job.done % 5 === 0) persistJobs();
        } catch (e) {
          failed += 1;
          log(job, `第 ${display} 张失败：${String(e.message || e).slice(0, 100)}`);
        }
      });
    }

    if (albumId && itemIds.length) {
      job.phase = '加入相册';
      persistJobs();
      await mediaApi('POST', '/api/admin/items/batch', { json: { ids: itemIds, action: 'album', value: albumId } });
    }

    job.missing = failed;
    if (job.done === 0) {
      job.status = 'error';
      job.error = '一张都没成功，检查密码、网络或图床可达性';
      job.phase = '失败';
    } else {
      job.status = 'done';
      job.phase = failed ? `完成，${failed} 张失败` : '完成';
    }
    log(job, `结束：成功 ${job.done}/${job.total}`);
    persistJobs();
  }

  async function pump() {
    if (state.running) return;
    state.running = true;
    try {
      while (true) {
        const job = state.jobs.find((j) => j.status === 'queued');
        if (!job) break;
        try {
          await runJob(job);
        } catch (e) {
          job.status = 'error';
          job.error = String(e.message || e).slice(0, 200);
          job.phase = '失败';
          log(job, '异常：' + job.error);
          persistJobs();
        }
      }
    } finally {
      state.running = false;
    }
  }

  // ============ 接口分派 ============
  async function handle(path, opts) {
    opts = opts || {};
    const u = new URL(path, location.origin);
    const p = u.pathname;
    const method = (opts.method || 'GET').toUpperCase();

    if (p === '/api/filters') return FILTERS;
    if (p === '/api/config') {
      if (method === 'POST') {
        const body = JSON.parse(opts.body || '{}');
        Object.keys(body).forEach((k) => {
          // 密码留空表示不修改
          if (k === 'media_password' && !body[k]) return;
          if (body[k] !== undefined && k !== 'jm_proxy') cfg[k] = body[k];
        });
        saveCfg();
        return { ok: true };
      }
      return {
        media_api: cfg.media_api, media_site: cfg.media_site,
        has_media_password: !!cfg.media_password,
        target_folder: cfg.target_folder, create_album: cfg.create_album,
        download_thread: cfg.download_thread, jm_proxy: '',
      };
    }
    if (p === '/api/ping') {
      const r = await fetch('/jm/ping');
      const d = await r.json();
      return d.ok ? 'ok' : 'fail';
    }
    if (p === '/api/search') {
      const q = (u.searchParams.get('q') || '').trim();
      const page = parseInt(u.searchParams.get('page') || '1', 10) || 1;
      const order = u.searchParams.get('order') || 'mr';
      const time = u.searchParams.get('time') || 'a';
      if (/^\d{3,8}$/.test(q)) {
        const album = await jmApi('/album?id=' + q);
        return { results: [mapItem(album)], total: 1, page: 1, is_id: true };
      }
      const qs = new URLSearchParams({ search_query: q, page: String(page), main_tag: '0', o: order, t: time });
      const data = await jmApi('/search?' + qs.toString());
      if (data.redirect_aid) {
        const album = await jmApi('/album?id=' + data.redirect_aid);
        return { results: [mapItem(album)], total: 1, page: 1, is_id: true };
      }
      return mapPage(data, page);
    }
    if (p === '/api/ranking') {
      const page = parseInt(u.searchParams.get('page') || '1', 10) || 1;
      const order = u.searchParams.get('order') || 'mv';
      const time = u.searchParams.get('time') || 'a';
      const o = time && time !== 'a' ? `${order}_${time}` : order;
      const qs = new URLSearchParams({ page: String(page), order: '', c: '0', o });
      const data = await jmApi('/categories/filter?' + qs.toString());
      return mapPage(data, page);
    }
    if (p === '/api/author') {
      const author = u.searchParams.get('author') || '';
      const page = parseInt(u.searchParams.get('page') || '1', 10) || 1;
      const qs = new URLSearchParams({ search_query: author, page: String(page), main_tag: '0', o: 'mr', t: 'a' });
      const data = await jmApi('/search?' + qs.toString());
      return mapPage(data, page);
    }
    if (p === '/api/download') {
      const ids = (JSON.parse(opts.body || '{}').ids || []).map(String);
      if (!ids.length) return { error: '没有选中任何本子' };
      if (!cfg.media_password) return { error: '请先在「设置」里填写影像馆后台密码' };
      const jobs = ids.map((id) => newJob(id));
      pump();
      return { jobs: jobs.map((j) => ({ id: j.id, jm_id: j.jm_id })) };
    }
    if (p === '/api/jobs') return { jobs: state.jobs.slice().reverse() };
    if (p === '/api/clear-jobs') {
      state.jobs = state.jobs.filter((j) => j.status === 'running' || j.status === 'queued');
      persistJobs();
      return { ok: true };
    }
    if (p === '/api/login-test') {
      try {
        mediaToken = '';
        await mediaLogin(true);
        return { ok: true, msg: '影像馆连接正常' };
      } catch (e) {
        return { ok: false, error: String(e.message || e) };
      }
    }
    throw new Error('未知接口：' + p);
  }

  window.JMApi = handle;
  // 调试出口：便于在控制台核验解密与重排结果
  window.JMDecode = { md5hex, decodePayload, segNum, processImage, descrambleBitmap };
})();
