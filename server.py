#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
禁漫天堂 → 影像馆 下载桥接服务
本地网页：搜索关键词 / JM号 -> 选本子 -> 下载图片 -> 上传进影像馆线上存储（R2 + D1）

纯 Python 标准库 + jmcomic，无需 Flask/FastAPI。
启动：python server.py  （默认 http://127.0.0.1:8899）
"""
import os, sys, json, re, time, uuid, threading, shutil, hashlib, traceback, mimetypes
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError as UrlHTTPError
import ssl
import sources  # 多源适配层：jm / wnacg / pica

# ---------------- 配置 ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
TMP_DIR = os.path.join(BASE_DIR, '.tmp')

DEFAULT_CONFIG = {
    "port": 8899,
    "media_api": "https://media-api.ixoxi.cn",   # 影像馆后端 API 地址
    "media_site": "https://media.ixoxi.cn",       # 影像馆前台（仅展示用）
    "media_password": "",                          # 影像馆后台密码（启动后在网页里填，不落盘）
    "target_folder": "",                          # 文件夹前缀，留空=直接用漫画名做文件夹
    "create_album": True,                          # 是否为每个本子建相册
    "jm_proxy": "",                                # 留空=继承环境代理；可填 http://127.0.0.1:7890
    "jm_image_suffix": "",                         # 留空=原格式；可填 .jpg/.png/.webp
    "jm_client_impl": "api",                       # api（推荐）/ html
    "download_thread": 16,                         # 图片并发下载数
    "source_proxy": "",                            # wnacg/pica 走的代理，留空=直连（容器部署留空）
    "pica_account": "",                            # 哔咔账号（存本地 config.json）
    "pica_password": "",                           # 哔咔密码（存本地 config.json，页面不回显）
}

# 允许持久化到 config.json 的字段（重启后自动恢复）
PERSIST_KEYS = ('media_api', 'media_site', 'target_folder', 'create_album',
                'jm_proxy', 'jm_image_suffix', 'jm_client_impl', 'download_thread',
                'source_proxy', 'pica_account', 'pica_password', 'media_password')


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                cfg.update(json.load(f))
        except Exception as e:
            print('[配置] 读取失败，用默认值:', e)
    # 环境变量覆盖（容器部署用）：PORT / MEDIA_API / MEDIA_SITE / ADMIN_PASSWORD / TARGET_FOLDER
    env_map = {
        'PORT': 'port', 'MEDIA_API': 'media_api', 'MEDIA_SITE': 'media_site',
        'ADMIN_PASSWORD': 'media_password', 'TARGET_FOLDER': 'target_folder',
        'CREATE_ALBUM': 'create_album', 'JM_PROXY': 'jm_proxy',
        'SOURCE_PROXY': 'source_proxy', 'PICA_ACCOUNT': 'pica_account',
        'PICA_PASSWORD': 'pica_password', 'DOWNLOAD_THREAD': 'download_thread',
    }
    for ek, ck in env_map.items():
        if ek in os.environ:
            v = os.environ[ek]
            if ck in ('port', 'download_thread'):
                v = int(v)
            elif ck in ('create_album',):
                v = v.strip().lower() in ('1', 'true', 'yes', 'on')
            cfg[ck] = v
    return cfg


def save_config():
    """把当前配置写入本地 config.json（原子替换），供重启后自动恢复。"""
    data = {k: CFG.get(k) for k in PERSIST_KEYS}
    try:
        tmp = CONFIG_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        print('[配置] 保存失败:', e)


CFG = load_config()
CFG_LOCK = threading.Lock()

# 运行时状态（不落盘）
RUNTIME = {
    'token': '',
    'token_exp': 0,
    'jobs': {},          # job_id -> job dict
    'jobs_lock': threading.Lock(),
}

# 排行榜缓存：type/page/category -> (expire_at, payload)，避免每次翻页都打源站
RANK_CACHE = {}
RANK_CACHE_LOCK = threading.Lock()
# 作者作品缓存：author/page -> (expire_at, payload)
AUTHOR_CACHE = {}
AUTHOR_CACHE_LOCK = threading.Lock()
RANK_MODES = {
    'hot':   ('a', 'mv'),   # 热门总榜：全时间 + 最多观看
    'day':   ('t', 'mv'),   # 日榜
    'week':  ('w', 'mv'),   # 周榜
    'month': ('m', 'mv'),   # 月榜
    'latest':('a', 'mr'),   # 最新上架
}

# 完整筛选项（与 jmcomic JmMagicConstants 对齐）
FILTER_ORDERS = [
    {'value': 'mr', 'label': '最新更新'},
    {'value': 'mv', 'label': '最多观看'},
    {'value': 'tf', 'label': '最多收藏'},
    {'value': 'md', 'label': '最多评论'},
    {'value': 'mp', 'label': '最多图片'},
    {'value': 'tr', 'label': '评分最高'},
]
FILTER_TIMES = [
    {'value': 'a', 'label': '全部时间'},
    {'value': 't', 'label': '今天'},
    {'value': 'w', 'label': '本周'},
    {'value': 'm', 'label': '本月'},
]
FILTER_CATEGORIES = [
    {'value': '0', 'label': '全部分类'},
    {'value': 'doujin', 'label': '同人'},
    {'value': 'single', 'label': '单本'},
    {'value': 'short', 'label': '短篇'},
    {'value': 'doujin_cosplay', 'label': '同人 Cosplay'},
    {'value': 'meiman', 'label': '美漫'},
    {'value': 'hanman', 'label': '韩漫'},
    {'value': '3D', 'label': '3D'},
    {'value': 'another', 'label': '其他'},
    {'value': 'english_site', 'label': '英文站'},
]
FILTER_SUBCATEGORIES = {
    '0': [{'value': '', 'label': '全部子分类'}],
    'doujin': [{'value': '', 'label': '全部子分类'}, {'value': 'CG', 'label': 'CG 插画'}, {'value': 'chinese', 'label': '汉化'}, {'value': 'japanese', 'label': '日文'}],
    'doujin_cosplay': [{'value': '', 'label': '全部子分类'}, {'value': 'cosplay', 'label': 'Cosplay'}, {'value': 'chinese', 'label': '汉化'}],
    'single': [{'value': '', 'label': '全部子分类'}, {'value': 'chinese', 'label': '汉化'}, {'value': 'japanese', 'label': '日文'}, {'value': 'youth', 'label': '少年'}],
    'short': [{'value': '', 'label': '全部子分类'}, {'value': 'chinese', 'label': '汉化'}, {'value': 'japanese', 'label': '日文'}],
    'meiman': [{'value': '', 'label': '全部子分类'}, {'value': 'chinese', 'label': '汉化'}],
    'hanman': [{'value': '', 'label': '全部子分类'}, {'value': 'chinese', 'label': '汉化'}],
    '3D': [{'value': '', 'label': '全部子分类'}, {'value': '3d', 'label': '3D'}],
    'another': [{'value': '', 'label': '全部子分类'}, {'value': 'other', 'label': '其他'}],
    'english_site': [{'value': '', 'label': '全部子分类'}, {'value': 'chinese', 'label': '汉化'}],
}
CATEGORY_IDS = {
    '0': '1', 'doujin': '1', 'doujin_cosplay': '1', 'english_site': '1',
    'single': '2', 'short': '3', '3D': '4', 'another': '4',
    'hanman': '5', 'meiman': '6',
}
SUB_KEYWORDS = {
    'chinese': ('汉化', '中文', '中国', 'chinese', '简体', '繁体'),
    'japanese': ('日文', '日本語', 'japanese', '无修', '無修正'),
    'youth': ('少年', 'youth'),
    'CG': ('cg', '插画', '画集'),
    'cosplay': ('cosplay', 'コス', 'cos'),
    '3d': ('3d',),
    'other': ('其他', 'other'),
}

# ---------------- jmcomic 客户端 ----------------
_jm = None
_jm_lock = threading.Lock()


def get_jm(base_dir=None):
    """构造 jmcomic Option；base_dir 指定则图片直接平铺到该目录（每任务独立，便于上传后清理）。"""
    jm = __import__('jmcomic')
    d = jm.JmOption.default().deconstruct()
    d['client']['impl'] = CFG['jm_client_impl']
    if CFG['jm_proxy']:
        d['client']['postman']['meta_data']['proxies'] = {
            'https': CFG['jm_proxy'], 'http': CFG['jm_proxy']
        }
    if CFG['jm_image_suffix']:
        d['download']['image']['suffix'] = CFG['jm_image_suffix']
    d['download']['threading']['image'] = int(CFG['download_thread'])
    if base_dir:
        # rule=Bd 只保留 base_dir 一层，图片直接平铺到该目录，不再套 <本子标题>/ 子目录
        d['dir_rule']['base_dir'] = base_dir
        d['dir_rule']['rule'] = 'Bd'
    else:
        d['dir_rule']['base_dir'] = TMP_DIR
    return jm.JmOption.construct(d)


def jm_client():
    return get_jm().new_jm_client()


def split_authors(value):
    """把作者字段拆成数组，兼容字符串/列表和多作者分隔符。"""
    if isinstance(value, (list, tuple, set)):
        parts = [str(x) for x in value]
    else:
        parts = re.split(r'[,，、/]', str(value or ''))
    out = []
    for x in parts:
        x = re.sub(r'^\s*[\[【(（]|[)\]】]\s*$', '', str(x)).strip()
        if x and x not in out:
            out.append(x)
    return out


def map_result_page(p, page=1):
    """把 jmcomic 的搜索/排行分页对象转成前端统一的结果结构。"""
    out = []
    for aid, info in (getattr(p, 'content', None) or []):
        tags = info.get('tags') or []
        if isinstance(tags, str):
            tags = [tags]
        authors = split_authors(info.get('author', ''))
        cat = info.get('category') or {}
        sub = info.get('category_sub') or {}
        out.append({
            'id': str(aid), 'title': info.get('name', ''),
            'author': ', '.join(authors),
            'authors': authors,
            'tags': tags[:8], 'pages': 0,
            'update': str(info.get('update_at', '')),
            'cover': f'/api/cover?id={aid}',
            'categoryId': str(cat.get('id') or ''),
            'categoryTitle': str(cat.get('title') or ''),
            'subCategoryId': str(sub.get('id') or ''),
            'subCategoryTitle': str(sub.get('title') or ''),
        })
    return {'results': out, 'total': getattr(p, 'total', len(out)), 'page': page}


def match_filters(item, category='0', sub_category=''):
    """对 API 不支持的 category/sub_category 做本地精确/关键字过滤。"""
    if category and category != '0':
        wanted = CATEGORY_IDS.get(category, '')
        if not wanted or str(item.get('categoryId') or '') != wanted:
            return False
    if sub_category:
        text = ' '.join([
            str(item.get('categoryTitle') or ''), str(item.get('subCategoryTitle') or ''),
            str(item.get('title') or ''), ' '.join(item.get('tags') or []),
        ]).lower()
        keywords = SUB_KEYWORDS.get(sub_category, (sub_category.lower(),))
        if not any(k.lower() in text for k in keywords):
            return False
    return True


def filtered_search_page(pager, page, category='0', sub_category='', max_pages=6):
    """搜索接口不支持分类参数，抓取多页后本地筛选并分页。"""
    rows = []
    page_size = 0
    for pno in range(1, max_pages + 1):
        data = map_result_page(pager(pno), pno)
        batch = data.get('results') or []
        if pno == 1:
            page_size = max(1, len(batch))
        elif not batch:
            break
        rows.extend([r for r in batch if match_filters(r, category, sub_category)])
        if len(rows) >= page * page_size:
            break
    start = max(0, page - 1) * page_size
    return {'results': rows[start:start + page_size], 'total': len(rows), 'page': page}


def normalize_filters(order='', time_range='', category='0', sub_category=''):
    """把前端传来的筛选值规整为合法值。"""
    orders = {x['value'] for x in FILTER_ORDERS}
    times = {x['value'] for x in FILTER_TIMES}
    categories = {x['value'] for x in FILTER_CATEGORIES}
    order = str(order or 'mr')
    time_range = str(time_range or 'a')
    category = str(category or '0')
    sub_category = str(sub_category or '')
    return (
        order if order in orders else 'mr',
        time_range if time_range in times else 'a',
        category if category in categories else '0',
        sub_category if sub_category in SUB_KEYWORDS else '',
    )


def ranking_page(mode, page, category='0', order='', time_range='', sub_category=''):
    """榜单浏览：支持完整排序/时间/分类/子分类，结果缓存 10 分钟。"""
    if mode not in RANK_MODES:
        mode = 'hot'
    default_time, default_order = RANK_MODES[mode]
    order, time_range, category, sub_category = normalize_filters(
        order or default_order, time_range or default_time, category, sub_category)
    key = (mode, int(page), category, order, time_range, sub_category)
    with RANK_CACHE_LOCK:
        hit = RANK_CACHE.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    client = jm_client()
    pager = lambda n: client.categories_filter(int(n), time_range, category, order)
    if sub_category:
        data = filtered_search_page(pager, int(page), '0', sub_category)
    else:
        data = map_result_page(pager(int(page)), int(page))
    data.update({'mode': mode, 'order': order, 'time': time_range,
                 'category': category, 'subCategory': sub_category, 'is_id': False})
    with RANK_CACHE_LOCK:
        if len(RANK_CACHE) > 240:
            now = time.time()
            for k in [k for k, v in RANK_CACHE.items() if v[0] <= now]:
                RANK_CACHE.pop(k, None)
        RANK_CACHE[key] = (time.time() + 600, data)
    return data


def author_page(author, page, order='mr', time_range='a', category='0', sub_category=''):
    """按作者名查作品，支持完整筛选，结果缓存 10 分钟。"""
    order, time_range, category, sub_category = normalize_filters(order, time_range, category, sub_category)
    key = (author, int(page), category, order, time_range, sub_category)
    with AUTHOR_CACHE_LOCK:
        hit = AUTHOR_CACHE.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    client = jm_client()
    pager = lambda n: client.search_site(author, page=int(n), order_by=order, time=time_range)
    if category != '0' or sub_category:
        data = filtered_search_page(pager, int(page), category, sub_category)
    else:
        data = map_result_page(pager(int(page)), int(page))
    data.update({'author': author, 'order': order, 'time': time_range,
                 'category': category, 'subCategory': sub_category, 'is_id': False})
    with AUTHOR_CACHE_LOCK:
        if len(AUTHOR_CACHE) > 240:
            now = time.time()
            for k in [k for k, v in AUTHOR_CACHE.items() if v[0] <= now]:
                AUTHOR_CACHE.pop(k, None)
        AUTHOR_CACHE[key] = (time.time() + 600, data)
    return data


# ---------------- 影像馆 API ----------------
# Cloudflare 会拦截无浏览器指纹的请求（403 挑战页），统一带上 UA/Origin/Referer
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


def site_headers():
    """按当前 API 域名补 Origin/Referer，伪装成后台页面发起的请求。"""
    h = dict(BROWSER_HEADERS)
    site = CFG.get('media_site', '').rstrip('/')
    if site:
        h['Origin'] = site
        h['Referer'] = site + '/'
    return h


def media_url(path):
    return CFG['media_api'].rstrip('/') + path


def media_login(force=False):
    """登录影像馆后台拿 token，7 天有效。密码从内存配置读。"""
    if not force and RUNTIME['token'] and RUNTIME['token_exp'] > time.time() + 600:
        return RUNTIME['token']
    pw = CFG['media_password']
    if not pw:
        raise RuntimeError('还没填影像馆后台密码，请在网页右上角「设置」里填写')
    body = json.dumps({'password': pw}).encode('utf-8')
    h = site_headers()
    h['Content-Type'] = 'application/json'
    req = Request(media_url('/api/admin/login'), data=body, headers=h, method='POST')
    try:
        with urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8'))
    except UrlHTTPError as e:
        detail = e.read().decode('utf-8', 'ignore')[:200]
        raise RuntimeError(f'影像馆登录失败（{e.code}）：{detail}')
    token = data.get('token')
    if not token:
        raise RuntimeError('影像馆登录没返回 token：' + str(data)[:200])
    RUNTIME['token'] = token
    RUNTIME['token_exp'] = time.time() + 6 * 86400
    return token


def media_api(method, path, data=None, raw=None, headers=None, retry=1):
    """带 Bearer token 调影像馆后台 API。raw=二进制上传体。"""
    token = media_login()
    h = site_headers()
    h['Authorization'] = 'Bearer ' + token
    if headers:
        h.update(headers)
    if raw is not None:
        body = raw
    elif data is not None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        h['Content-Type'] = 'application/json'
    else:
        body = None
    req = Request(media_url(path), data=body, headers=h, method=method)
    try:
        with urlopen(req, timeout=120) as r:
            out = r.read().decode('utf-8', 'ignore')
            return json.loads(out) if out else {}
    except UrlHTTPError as e:
        if e.code == 401 and retry:
            RUNTIME['token'] = ''
            return media_api(method, path, data, raw, headers, retry - 1)
        detail = e.read().decode('utf-8', 'ignore')[:300]
        raise RuntimeError(f'影像馆 API {method} {path} 失败（{e.code}）：{detail}')


def clean_part(s, limit=60):
    """清理单段名称：路径非法字符替换为 -，压缩空白。"""
    s = re.sub(r'[\\/:*?"<>|]', '-', str(s or ''))
    s = re.sub(r'\s+', ' ', s).strip(' -')
    return s[:limit]


def extract_author(title):
    """标题没带作者字段时，从首个 [作者] 里猜出作者名。"""
    for block in re.findall(r'[\[【]([^\]】]+)[\]】]', title or ''):
        b = block.strip()
        if not b:
            continue
        # 跳过汉化组/DL版/画质标注等非作者标签
        if re.search(r'(DL版|汉化|翻訳|翻译|中文|中国|完结|单行本|全彩|无?修正|無修正|\d+\s*P$)', b, re.I):
            continue
        return b
    return ''


def comic_name(title, jm_id='', author=''):
    """生成文件夹/相册/图片文件名：JM号 - 作者 - 漫画名。

    例：[Numathno. (降夜むく)] semi colon (どうぶつの森) [中国翻译] [DL版], 1492816
        -> JM1492816 - Numathno. (降夜むく) - semi colon どうぶつの森
    """
    raw = (title or '').strip()
    # 作者：优先用详情接口的 authors；没有就从标题方括号里提取
    a = clean_part(author, 40) or clean_part(extract_author(raw), 40)
    # 漫画名：去掉 [作者/汉化/DL版] 【标签】 方括号内容；圆括号多为作品名的一部分，保留
    core = clean_part(re.sub(r'[\[【][^\]】]*[\]】]', ' ', raw), 60)
    parts = ['JM' + str(jm_id)] if jm_id else []
    if a:
        parts.append(a)
    if core:
        parts.append(core)
    return ' - '.join(parts) or ('JM' + str(jm_id))


def new_key(kind, name):
    """复刻影像馆前端 newKey：image/年/月/随机id-安全文件名"""
    rid = uuid.uuid4().hex[:10]
    safe = re.sub(r'[^\w.\-一-鿿]', '_', name)[:80] or 'file'
    t = time.localtime()
    return f"{kind}/{t.tm_year}/{t.tm_mon:02d}/{rid}-{safe}"


def upload_and_register(file_path, title, folder, tags=None, display_name=None):
    """上传单个文件到 R2 并写入 D1 items 记录，返回 item id。"""
    name = display_name or os.path.basename(file_path)
    key = new_key('image', name)
    mime = mimetypes.guess_type(name)[0] or 'image/jpeg'
    with open(file_path, 'rb') as f:
        raw = f.read()
    from urllib.parse import urlencode
    qs = urlencode({'key': key, 'name': name, 'store': 'r2'})
    media_api('PUT', f'/api/admin/upload?{qs}',
              raw=raw, headers={'Content-Type': mime})
    # 后台入库、前台不展示：hidden=1 且 inGallery=0
    # 后台管理页仍可查看/管理，前台画廊、文件夹浏览都看不到
    payload = {
        'kind': 'image', 'key': key, 'size': len(raw),
        'name': name, 'title': title or name, 'mime': mime,
        'folder': folder, 'tags': tags or [],
        'hidden': True, 'inGallery': False,
    }
    item = media_api('POST', '/api/admin/items', data=payload)
    return item.get('id'), key


def get_or_create_album(title, description=''):
    """按标题找相册，没有就建一个。"""
    albums = media_api('GET', '/api/admin/albums')
    for a in albums if isinstance(albums, list) else albums.get('list', []):
        if a.get('title') == title:
            return a.get('id')
    a = media_api('POST', '/api/admin/albums',
                  data={'title': title, 'description': description, 'hidden': True})
    return a.get('id')


def add_to_album(item_ids, album_id):
    if not album_id or not item_ids:
        return
    media_api('POST', '/api/admin/items/batch',
              data={'ids': item_ids, 'action': 'album', 'value': album_id})


# ---------------- 下载完整性 ----------------
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif')


def collect_images(work):
    """收集任务目录里的所有图片，按文件名排序。"""
    out = []
    for root, _, files in os.walk(work):
        for fn in files:
            if fn.lower().endswith(IMAGE_EXTS):
                out.append(os.path.join(root, fn))
    out.sort()
    return out


def fill_missing_images(client, jm_id, work, log):
    """逐张核对详情页，补齐 jmcomic 批量下载时漏掉的图。

    jmcomic 的 download_album 在单张请求失败时会跳过，不会抛错。
    这里按 photo -> image 明细逐张对比磁盘文件，缺哪张补哪张，每张最多重试 3 次。
    """
    filled = 0
    try:
        album = client.get_album_detail(jm_id)
    except Exception as e:
        log('补图：获取本子详情失败 ' + str(e)[:80])
        return 0
    existing = {os.path.basename(p) for p in collect_images(work)}
    for photo in (album if album else []):
        pid = getattr(photo, 'photo_id', None)
        if not pid:
            continue
        try:
            p = client.get_photo_detail(pid, fetch_album=False, fetch_scramble_id=True)
        except Exception as e:
            log(f'补图：章节 {pid} 详情获取失败 {str(e)[:60]}')
            continue
        for i in range(len(p.page_arr)):
            try:
                img = p.create_image_detail(i)
                fn = img.filename
            except Exception:
                continue
            if fn in existing:
                continue
            target = os.path.join(work, fn)
            for attempt in range(1, 4):
                try:
                    client.download_by_image_detail(img, target)
                    existing.add(fn)
                    filled += 1
                    log(f'补齐第 {i+1} 张：{fn}')
                    break
                except Exception as e:
                    if attempt == 3:
                        log(f'补齐失败 {fn}：{str(e)[:80]}')
                    else:
                        time.sleep(attempt)
    return filled


# ---------------- 多源任务执行 ----------------
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif')


def run_source_job(job):
    """wnacg / pica 的通用任务：解析图片直链 -> 并发下载 -> 上传影像馆。"""
    source = job.get('source') or 'wnacg'
    aid = str(job.get('item_id') or '')
    jid = job['id']

    def log(msg):
        with RUNTIME['jobs_lock']:
            job['logs'].append(msg)
            job['logs'] = job['logs'][-100:]
        print(f'[{jid[:6]}] {msg}')

    def set_status(s, **kw):
        with RUNTIME['jobs_lock']:
            job['status'] = s
            job.update(kw)

    work = os.path.join(TMP_DIR, jid)
    os.makedirs(work, exist_ok=True)
    try:
        set_status('running', phase='解析图片地址')
        log(f'开始采集 {sources.SOURCE_LABELS.get(source, source)} {aid}')
        images, detail = sources.source_all_images(source, aid, CFG, log)
        title = detail.get('title') or f'{source}{aid}'
        author = detail.get('author') or ''
        job['title'] = title
        job['author'] = author
        # 统一命名：<源前缀><编号> - 作者 - 漫画名
        prefix = f'{source.upper()}{aid}'
        cname = re.sub(r'[\\/:*?"<>|]', '-', f'{prefix} - {author} - {title}')
        cname = re.sub(r'\s+', ' ', cname).strip(' -')[:80]
        job['comic_name'] = cname
        log('标题：' + title[:60])
        log('文件夹/文件名：' + cname)
        job['expected'] = len(images)
        if not images:
            raise RuntimeError('没有解析到图片地址')
        log(f'共 {len(images)} 张，开始下载')

        set_status('running', phase='下载图片')
        proxy = sources.get_source_config(CFG)['proxy']
        failed = []
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def fetch(item, attempt=1):
            ext = os.path.splitext(urllib.parse.urlparse(item['url']).path)[1] or '.jpg'
            if ext.lower() not in IMAGE_EXTS:
                ext = '.jpg'
            fp = os.path.join(work, f"{item['index']:05d}{ext}")
            if os.path.exists(fp) and os.path.getsize(fp) > 1000:
                return fp
            referer = (sources.WNACG_HOST + '/' if source == 'wnacg'
                       else 'https://www.picacomic.com/' if source == 'pica' else '')
            data = sources.http_get(item['url'], proxy=proxy,
                                    headers={'Referer': referer, 'User-Agent': 'Mozilla/5.0'},
                                    timeout=45, retries=2, raw=True)
            if not data or len(data) < 1000:
                raise RuntimeError('图片内容为空')
            with open(fp, 'wb') as f:
                f.write(data)
            return fp

        with ThreadPoolExecutor(max_workers=max(2, int(CFG.get('download_thread', 12)))) as ex:
            futures = {ex.submit(fetch, it): it for it in images}
            done_count = 0
            for fut in as_completed(futures):
                it = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    failed.append(it)
                    if len(failed) == 1:
                        log(f'下载异常样例：{type(e).__name__}: {str(e)[:160]}')
                done_count += 1
                if done_count % 10 == 0 or done_count == len(images):
                    with RUNTIME['jobs_lock']:
                        job['done'] = done_count
                        job['total'] = len(images)
                    log(f'下载进度 {done_count}/{len(images)}')
        # 失败重试（逐张，最多 3 次）
        if failed:
            log(f'首轮失败 {len(failed)} 张，开始重试')
            for attempt in range(1, 4):
                still = []
                for it in failed:
                    try:
                        fetch(it)
                    except Exception:
                        still.append(it)
                failed = still
                if not failed:
                    break
                log(f'第 {attempt} 轮重试后仍失败 {len(failed)} 张')
                time.sleep(attempt)
        local = collect_images(work)
        job['missing'] = max(0, len(images) - len(local))
        log(f'下载完成：{len(local)}/{len(images)} 张' + (f'，缺 {job["missing"]} 张' if job['missing'] else ''))

        set_status('running', phase='上传影像馆')
        prefix_cfg = CFG.get('target_folder', '').strip().strip('/')
        folder = (prefix_cfg + '/' + cname) if prefix_cfg else cname
        log('影像馆文件夹：' + folder)
        album_id = None
        if CFG['create_album']:
            album_id = get_or_create_album(cname, f'{sources.SOURCE_LABELS.get(source, source)} {aid} · {title[:100]}')
        item_ids = []
        tags = [sources.SOURCE_LABELS.get(source, source), f'{source.upper()}{aid}']
        for i, fp in enumerate(local, 1):
            ext = os.path.splitext(fp)[1] or '.jpg'
            m = re.search(r'(\d+)', os.path.basename(fp))
            idx = int(m.group(1)) if m else i
            disp = f'{cname}-{idx:05d}{ext}'
            try:
                iid, _ = upload_and_register(fp, f'{cname} - {idx:05d}', folder, tags, display_name=disp)
                if iid:
                    item_ids.append(iid)
            except Exception as e:
                log(f'第 {i}/{len(local)} 张上传失败：{str(e)[:100]}')
            with RUNTIME['jobs_lock']:
                job['done'] = i
                job['total'] = len(local)
            if i % 10 == 0 or i == len(local):
                log(f'上传进度 {i}/{len(local)}')
        if album_id:
            add_to_album(item_ids, album_id)
        set_status('done', phase='完成', done=len(local), total=len(local), item_count=len(item_ids))
        log(f'全部完成：{len(item_ids)} 张已入库，相册 id={album_id}')
    except Exception as e:
        set_status('error', phase='失败', error=str(e)[:400])
        log('失败：' + str(e)[:200])
        log(traceback.format_exc()[-500:])
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------- 任务执行 ----------------
def run_job(job):
    """后台线程：下载本子 -> 上传影像馆。"""
    jid = job['id']
    jm_id = job['jm_id']
    def log(msg):
        with RUNTIME['jobs_lock']:
            job['logs'].append(msg)
            job['logs'] = job['logs'][-100:]
        print(f'[{jid[:6]}] {msg}')
    def set_status(s, **kw):
        with RUNTIME['jobs_lock']:
            job['status'] = s
            job.update(kw)

    work = os.path.join(TMP_DIR, jid)
    os.makedirs(work, exist_ok=True)
    try:
        set_status('running', phase='获取本子信息')
        log('开始下载 JM' + str(jm_id))
        client = jm_client()
        author = ''
        detail = None
        try:
            detail = client.get_album_detail(jm_id)
            title = detail.name if detail else ('JM' + str(jm_id))
            if detail:
                authors = getattr(detail, 'authors', None) or []
                if isinstance(authors, str):
                    author = authors
                else:
                    author = ', '.join(str(x) for x in authors if x)
        except Exception:
            title = 'JM' + str(jm_id)
        job['title'] = title
        job['author'] = author
        # 每本漫画独立文件夹/相册，文件名保留 JM号 + 作者 + 漫画名
        cname = comic_name(title, jm_id, author)
        job['comic_name'] = cname
        log('本子标题：' + title[:60])
        log('作者：' + (author or '未取到，尝试从标题提取'))
        log('文件夹/文件名：' + cname)

        set_status('running', phase='下载图片')
        # 每任务独立目录，rule=Bd 让图片平铺到 work/ 下
        opt = get_jm(work)
        expected = int(getattr(detail, 'page_count', 0) or 0) if detail else 0
        job['expected'] = expected
        # jmcomic 单张失败会静默跳过，先整本重下 3 轮（cache=True 会跳过已存在文件）
        for attempt in range(1, 4):
            opt.download_album(jm_id)
            images = collect_images(work)
            log(f'下载第 {attempt} 轮：{len(images)}' + (f'/{expected}' if expected else '') + ' 张')
            if expected and len(images) >= expected:
                break
            if attempt < 3:
                log('检测到缺页，自动重试补齐…')
                time.sleep(attempt)
        # 整本重下仍缺页时，按图片明细逐张定点补齐
        if expected and len(images) < expected:
            set_status('running', phase='补齐缺页')
            log(f'仍有 {expected - len(images)} 张缺失，开始逐张补齐')
            filled = fill_missing_images(client, jm_id, work, log)
            images = collect_images(work)
            log(f'逐张补齐完成，新增 {filled} 张，当前 {len(images)}/{expected}')
        job['missing'] = max(0, expected - len(images)) if expected else 0
        if job['missing']:
            log(f'警告：仍缺 {job["missing"]} 张，先上传已下载部分')
        if not images:
            raise RuntimeError('没找到下载的图片（下载可能失败）')
        log(f'图片下载完成（{len(images)} 张），开始上传影像馆')

        set_status('running', phase='上传影像馆')

        # 文件夹 = 漫画名（可选加前缀）；相册名 = 漫画名
        prefix = CFG.get('target_folder', '').strip().strip('/')
        folder = (prefix + '/' + cname) if prefix else cname
        log('影像馆文件夹：' + folder)
        album_id = None
        if CFG['create_album']:
            album_title = cname
            album_id = get_or_create_album(album_title, f'禁漫天堂 JM{jm_id} · {title[:100]}')

        item_ids = []
        total = len(images)
        for i, fp in enumerate(images, 1):
            ext = os.path.splitext(fp)[1] or '.jpg'
            # 用源文件名里的页码，缺页时不会把后面的图往前挤
            m = re.search(r'(\d+)', os.path.basename(fp))
            idx = int(m.group(1)) if m else i
            disp = f'{cname}-{idx:03d}{ext}'
            try:
                iid, _ = upload_and_register(fp, f'{cname} - {idx:03d}', folder,
                                              ['禁漫', f'JM{jm_id}'], display_name=disp)
                if iid:
                    item_ids.append(iid)
            except Exception as e:
                log(f'第 {i}/{total} 张上传失败：{str(e)[:100]}')
            with RUNTIME['jobs_lock']:
                job['done'] = i
                job['total'] = total
            if i % 10 == 0 or i == total:
                log(f'上传进度 {i}/{total}')

        if album_id:
            add_to_album(item_ids, album_id)
        set_status('done', phase='完成', done=total, total=total, item_count=len(item_ids))
        log(f'全部完成：{len(item_ids)} 张已入库，相册 id={album_id}')
    except Exception as e:
        set_status('error', phase='失败', error=str(e)[:400])
        log('失败：' + str(e)[:200])
        log(traceback.format_exc()[-500:])
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------- HTTP 服务 ----------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path):
        with open(path, 'rb') as f:
            data = f.read()
        ct = 'text/html; charset=utf-8' if path.endswith('.html') else 'application/octet-stream'
        if path.endswith('.js'):
            ct = 'application/javascript; charset=utf-8'
        elif path.endswith('.css'):
            ct = 'text/css; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path
        if path in ('/', '/index.html'):
            return self.send_file(os.path.join(BASE_DIR, 'index.html'))
        # 本地内置的第三方组件（Tailwind / Lucide / Alpine），不依赖公网 CDN
        if path.startswith('/assets/'):
            name = os.path.basename(path)
            if name in ('tailwind.js', 'lucide.js', 'alpine.js'):
                fp = os.path.join(BASE_DIR, 'assets', name)
                if os.path.isfile(fp):
                    return self.send_file(fp)
            return self.send_json({'error': 'not found'}, 404)
        if path == '/api/config':
            safe = dict(CFG)
            safe['has_password'] = bool(CFG['media_password'])
            safe['media_password'] = ''
            safe['has_pica_password'] = bool(CFG.get('pica_password'))
            safe['pica_password'] = ''
            safe['has_source_proxy'] = bool(CFG.get('source_proxy'))
            return self.send_json(safe)
        if path == '/api/filters':
            return self.send_json({
                'orders': FILTER_ORDERS, 'times': FILTER_TIMES,
                'categories': FILTER_CATEGORIES, 'subCategories': FILTER_SUBCATEGORIES,
                'sources': [{'value': s, 'label': sources.SOURCE_LABELS[s]} for s in sources.SOURCES],
                'sourceCategories': {
                    'jm': FILTER_CATEGORIES,
                    'wnacg': [{'value': k, 'label': v} for k, v in [
                        ('0', '全部分类'), ('doujin', '同人誌'), ('single', '单行本'),
                        ('short', '杂志短篇'), ('cg', 'CG画集'), ('3D', '3D漫画'),
                        ('cosplay', 'Cosplay'), ('japanese', '日语'), ('chinese', '汉化'),
                        ('english', '英文'), ('ai', 'AI图集'), ('magazine', '杂志')]],
                    'pica': [{'value': '0', 'label': '全部'}],
                },
            })
        if path == '/api/search':
            kw = (q.get('q', [''])[0] or '').strip()
            try:
                page = int(q.get('page', ['1'])[0] or 1)
            except ValueError:
                page = 1
            page = max(1, min(page, 50))
            order, time_range, category, sub_category = normalize_filters(
                q.get('order', [''])[0], q.get('time', [''])[0],
                q.get('category', ['0'])[0], q.get('subCategory', [''])[0])
            if not kw:
                return self.send_json({'error': '请输入关键词'}, 400)
            source = (q.get('source', ['jm'])[0] or 'jm').strip().lower()
            if source in ('wnacg', 'pica'):
                try:
                    data = sources.source_search(source, kw, page, CFG,
                                                 order, time_range, category, sub_category)
                    # wnacg / pica 的纯数字视为编号直接查详情
                    if source == 'wnacg' and re.fullmatch(r'\d{3,9}', kw):
                        try:
                            d = sources.wnacg_detail(kw, sources.get_source_config(CFG)['proxy'])
                            data = {'results': [{
                                'id': d['id'], 'title': d['title'],
                                'author': d['author'], 'authors': d['authors'],
                                'tags': d['tags'], 'pages': d['pages'],
                                'cover': f"/api/cover?source=wnacg&id={d['id']}"
                                         + (('&u=' + urllib.parse.quote(d['cover_url'], safe='')) if d['cover_url'] else ''),
                            }], 'total': 1, 'page': 1, 'is_id': True, 'source': 'wnacg'}
                        except Exception as de:
                            return self.send_json({'error': '查询编号失败：' + str(de)[:300]}, 500)
                    return self.send_json(data)
                except Exception as e:
                    return self.send_json({'error': str(e)[:300]}, 500)
            is_id = bool(re.fullmatch(r'\d{3,9}', kw))
            try:
                client = jm_client()
                if is_id:
                    d = client.get_album_detail(kw)
                    authors = split_authors(getattr(d, 'authors', None) or []) if d else []
                    results = [{
                        'id': str(d.album_id), 'title': d.name,
                        'author': ', '.join(authors),
                        'authors': authors,
                        'tags': d.tags or [], 'pages': d.page_count,
                        'cover': f'/api/cover?id={d.album_id}',
                    }] if d else []
                    return self.send_json({'results': results, 'total': len(results), 'page': 1, 'is_id': True})
                pager = lambda n: client.search_site(kw, page=n, order_by=order, time=time_range)
                if category != '0' or sub_category:
                    data = filtered_search_page(pager, page, category, sub_category)
                else:
                    data = map_result_page(pager(page), page)
                data.update({'is_id': False, 'order': order, 'time': time_range,
                             'category': category, 'subCategory': sub_category})
                return self.send_json(data)
            except Exception as e:
                return self.send_json({'error': str(e)[:300]}, 500)
        if path == '/api/ranking':
            mode = (q.get('type', ['hot'])[0] or 'hot').strip().lower()
            try:
                page = int(q.get('page', ['1'])[0] or 1)
            except ValueError:
                page = 1
            category = (q.get('category', ['0'])[0] or '0').strip()
            order = (q.get('order', [''])[0] or '').strip()
            time_range = (q.get('time', [''])[0] or '').strip()
            sub_category = (q.get('subCategory', [''])[0] or '').strip()
            page = max(1, min(page, 50))
            source = (q.get('source', ['jm'])[0] or 'jm').strip().lower()
            if source in ('wnacg', 'pica'):
                try:
                    data = sources.source_ranking(source, mode, page, CFG, category,
                                                  order=order, time_range=time_range,
                                                  sub_category=sub_category)
                    data['is_id'] = False
                    return self.send_json(data)
                except Exception as e:
                    return self.send_json({'error': str(e)[:300]}, 500)
            try:
                data = ranking_page(mode, page, category, order, time_range, sub_category)
                data['is_id'] = False
                return self.send_json(data)
            except Exception as e:
                return self.send_json({'error': str(e)[:300]}, 500)
        if path == '/api/author':
            author = (q.get('author', [''])[0] or '').strip()
            if not author:
                return self.send_json({'error': '缺少作者名'}, 400)
            try:
                page = int(q.get('page', ['1'])[0] or 1)
            except ValueError:
                page = 1
            page = max(1, min(page, 50))
            order = (q.get('order', [''])[0] or '').strip()
            time_range = (q.get('time', [''])[0] or '').strip()
            category = (q.get('category', ['0'])[0] or '0').strip()
            sub_category = (q.get('subCategory', [''])[0] or '').strip()
            source = (q.get('source', ['jm'])[0] or 'jm').strip().lower()
            if source in ('wnacg', 'pica'):
                try:
                    return self.send_json(sources.source_author(
                        source, author, page, CFG, order=order, time_range=time_range,
                        category=category, sub_category=sub_category))
                except Exception as e:
                    return self.send_json({'error': str(e)[:300]}, 500)
            try:
                return self.send_json(author_page(author, page, order, time_range, category, sub_category))
            except Exception as e:
                return self.send_json({'error': str(e)[:300]}, 500)
        if path == '/api/cover':
            jm_id = (q.get('id', [''])[0] or '').strip()
            source = (q.get('source', ['jm'])[0] or 'jm').strip().lower()
            direct = (q.get('u', [''])[0] or '').strip()
            if source in ('wnacg', 'pica'):
                try:
                    ck = f'cover_{source}_{jm_id}.img'
                    cf = os.path.join(TMP_DIR, ck)
                    if os.path.exists(cf) and time.time() - os.path.getmtime(cf) < 86400:
                        img = open(cf, 'rb').read()
                    else:
                        img = sources.source_cover(source, jm_id, CFG, direct)
                        if not img:
                            return self.send_json({'error': '封面获取失败'}, 404)
                        os.makedirs(TMP_DIR, exist_ok=True)
                        with open(cf, 'wb') as f:
                            f.write(img)
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', str(len(img)))
                    self.send_header('Cache-Control', 'max-age=86400')
                    self.end_headers()
                    self.wfile.write(img)
                except Exception as e:
                    return self.send_json({'error': str(e)[:200]}, 404)
                return
            try:
                if not jm_id.isdigit():
                    return self.send_json({'error': '无效 JM 号'}, 400)
                # 禁漫封面固定规则：{图床域名}/media/albums/{id}.jpg
                # 图床域名从 jmcomic 配置拿；带磁盘缓存避免每次都请求
                import jmcomic
                hosts = list(jmcomic.JmModuleConfig.DOMAIN_IMAGE_LIST or [])
                cache_key = f'cover_{jm_id}.jpg'
                cache_file = os.path.join(TMP_DIR, cache_key)
                if os.path.exists(cache_file) and time.time() - os.path.getmtime(cache_file) < 86400:
                    img = open(cache_file, 'rb').read()
                else:
                    img = None
                    ctx = ssl._create_unverified_context()
                    for host in hosts[:4]:
                        for suffix in ('jpg', 'webp', 'png'):
                            url = f'https://{host}/media/albums/{jm_id}.{suffix}'
                            try:
                                req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                                with urlopen(req, timeout=8, context=ctx) as r:
                                    data = r.read()
                                if len(data) > 1000:
                                    img = data
                                    break
                            except Exception:
                                continue
                        if img:
                            break
                    if not img:
                        return self.send_json({'error': '封面获取失败'}, 404)
                    os.makedirs(TMP_DIR, exist_ok=True)
                    with open(cache_file, 'wb') as f:
                        f.write(img)
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(img)))
                self.send_header('Cache-Control', 'max-age=86400')
                self.end_headers()
                self.wfile.write(img)
            except Exception as e:
                return self.send_json({'error': str(e)[:200]}, 404)
            return
        if path == '/api/jobs':
            with RUNTIME['jobs_lock']:
                return self.send_json({'jobs': list(RUNTIME['jobs'].values())})
        if path == '/api/job':
            jid = (q.get('id', [''])[0] or '')
            with RUNTIME['jobs_lock']:
                return self.send_json(RUNTIME['jobs'].get(jid, {'error': '任务不存在'}), 404 if jid not in RUNTIME['jobs'] else 200)
        if path == '/api/ping':
            return self.send_json({'ok': True, 'time': int(time.time())})
        return self.send_json({'error': 'not found'}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        length = int(self.headers.get('Content-Length', 0) or 0)
        raw = self.rfile.read(length) if length else b''
        try:
            data = json.loads(raw.decode('utf-8')) if raw else {}
        except Exception:
            data = {}

        if path == '/api/config':
            # 只更新允许的配置项，密码只存内存
            allowed = ['media_api', 'media_site', 'target_folder', 'create_album',
                       'jm_proxy', 'jm_image_suffix', 'jm_client_impl', 'download_thread',
                       'source_proxy']
            # 账号密码只存内存
            for k in ('pica_account', 'pica_password'):
                if k in data and data[k]:
                    CFG[k] = data[k]
            with CFG_LOCK:
                for k in allowed:
                    if k in data:
                        CFG[k] = data[k]
                if 'media_password' in data and data['media_password']:
                    CFG['media_password'] = data['media_password']
                    RUNTIME['token'] = ''  # 密码变了，token 失效
                # 端口不改（需重启）
            save_config()
            return self.send_json({'ok': True})

        if path == '/api/login-test':
            try:
                media_login(force=True)
                return self.send_json({'ok': True, 'msg': '影像馆登录成功'})
            except Exception as e:
                return self.send_json({'ok': False, 'error': str(e)[:300]}, 400)

        if path == '/api/download':
            ids = data.get('ids') or []
            if isinstance(ids, str):
                ids = [ids]
            ids = [str(i).strip() for i in ids if str(i).strip()][:20]
            if not ids:
                return self.send_json({'error': '没有选本子'}, 400)
            source = (data.get('source') or 'jm').strip().lower()
            jobs = []
            for jm_id in ids:
                jid = uuid.uuid4().hex
                job = {
                    'id': jid, 'jm_id': jm_id, 'item_id': jm_id, 'source': source,
                    'status': 'queued', 'phase': '排队中', 'title': '', 'done': 0, 'total': 0,
                    'logs': [], 'error': '', 'created': int(time.time()),
                }
                with RUNTIME['jobs_lock']:
                    RUNTIME['jobs'][jid] = job
                target = run_source_job if source in ('wnacg', 'pica') else run_job
                t = threading.Thread(target=target, args=(job,), daemon=True)
                t.start()
                jobs.append({'id': jid, 'jm_id': jm_id, 'source': source})
            return self.send_json({'ok': True, 'jobs': jobs})

        if path == '/api/clear-jobs':
            with RUNTIME['jobs_lock']:
                RUNTIME['jobs'].clear()
            return self.send_json({'ok': True})

        return self.send_json({'error': 'not found'}, 404)


def main():
    os.makedirs(TMP_DIR, exist_ok=True)
    port = int(CFG.get('port', 8899))
    srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'========================================')
    print(f' 禁漫下载器已启动')
    print(f' 网页： http://localhost:{port}')
    print(f' 影像馆： {CFG["media_api"]}')
    print(f' 目标文件夹： {CFG["target_folder"]}')
    print(f'========================================')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
        srv.shutdown()


if __name__ == '__main__':
    main()
