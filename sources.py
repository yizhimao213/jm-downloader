#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多源适配层：jm（禁漫天堂）/ wnacg（绅士漫画）/ pica（哔咔漫画）。

每个源对外提供统一结构：
  results: [{id, title, author, authors, tags, pages, update, cover}, ...]
  cover:   /api/cover?source=<src>&id=<id>&u=<可选直链>
所有的网络访问统一走 http_get/http_post（带重试 + 可选代理）。
"""
import os
import re
import ssl
import json
import time
import html
import uuid
import random
import string
import hashlib
import hmac
import urllib.parse
from urllib.request import Request, urlopen, build_opener, ProxyHandler, HTTPSHandler
from urllib.error import HTTPError as UrlHTTPError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, '.tmp')
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ---------------- 通用 HTTP ----------------
def _friendly(e, url):
    """把底层网络异常翻译成可读提示，避免前端只看到 WinError 10061。"""
    host = urllib.parse.urlparse(url).netloc
    s = str(e)
    if '10061' in s or 'refused' in s.lower():
        return f'无法连接代理/源站（{host}）：连接被拒绝，请检查 source_proxy 是否可用'
    if 'timed out' in s.lower() or 'timeout' in s.lower():
        return f'连接 {host} 超时：当前网络出口不可达，该源需境外出口'
    if 'Name or service not known' in s or 'getaddrinfo' in s:
        return f'域名解析失败（{host}）：DNS 不可用'
    return f'{host}：{s[:200]}'


def make_opener(proxy=''):
    proxy = (proxy or '').strip()
    if proxy:
        return build_opener(ProxyHandler({'http': proxy, 'https': proxy}),
                            HTTPSHandler(context=_SSL_CTX))
    return build_opener(HTTPSHandler(context=_SSL_CTX))


def http_get(url, proxy='', headers=None, timeout=18, retries=2, raw=False):
    h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    if headers:
        h.update(headers)
    op = make_opener(proxy)
    last = None
    for i in range(retries):
        try:
            with op.open(Request(url, headers=h), timeout=timeout) as r:
                data = r.read()
            return data if raw else data.decode('utf-8', 'ignore')
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1 + i)
    raise RuntimeError(_friendly(last, url))


def http_post(url, body, proxy='', headers=None, timeout=18, retries=2):
    h = {'Content-Type': 'application/json; charset=UTF-8',
         'User-Agent': 'okhttp/3.8.1'}
    if headers:
        h.update(headers)
    op = make_opener(proxy)
    data = json.dumps(body or {}, ensure_ascii=False).encode('utf-8')
    last = None
    for i in range(retries):
        try:
            with op.open(Request(url, data=data, headers=h, method='POST'),
                         timeout=timeout) as r:
                return r.read().decode('utf-8', 'ignore')
        except UrlHTTPError as e:
            # 4xx 业务错误不重试，直接返回响应体
            try:
                return e.read().decode('utf-8', 'ignore')
            except Exception:
                raise
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1 + i)
    raise RuntimeError(_friendly(last, url))


def clean_text(s):
    return html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(s or '')))).strip()


# ================================================================
# wnacg（绅士漫画）：HTML 解析，匿名可用
# ================================================================
WNACG_HOST = 'https://www.wnacg.com'


def _wn_url(path):
    return WNACG_HOST + (path if path.startswith('/') else '/' + path)


def _wn_cards(page_html, base_id=''):
    """解析列表/搜索页卡片。"""
    out = []
    # 卡片：<li class="li gallary_item"> ... <a href="/photos-index-aid-88299.html" title="标题">
    for m in re.finditer(r'<li class="li gallary_item">(.*?)</li>', page_html, re.S):
        card = m.group(1)
        a = re.search(r'<a href="(/photos-index-aid-\d+\.html)" title="([^"]*)"', card)
        if not a:
            a = re.search(r'<a href="(/photos-index-aid-\d+\.html)"[^>]*>\s*([^<]+)', card)
        if not a:
            continue
        aid = (re.search(r'aid-(\d+)', a.group(1)) or [None, ''])[1]
        title = clean_text(a.group(2))
        img = re.search(r'<img[^>]+src="([^"]+)"', card)
        cover_url = ''
        if img:
            cover_url = img.group(1).strip()
            if cover_url.startswith('//'):
                cover_url = 'https:' + cover_url
            elif cover_url.startswith('/'):
                cover_url = _wn_url(cover_url)
        info = re.search(r'<div class="info_col">(.*?)</div>', card, re.S)
        info_txt = clean_text(info.group(1)) if info else ''
        pages = int((re.search(r'(\d+)\s*張圖片', info_txt) or [None, '0'])[1] or 0)
        update = ''
        um = re.search(r'(\d{4}-\d{2}-\d{2})', info_txt)
        if um:
            update = um.group(1)
        # 从标题首个 [作者] 提取
        author = ''
        am = re.match(r'^\[([^\]]{1,40})\]', title)
        if am:
            author = am.group(1).strip()
        out.append({
            'id': str(aid), 'title': title, 'author': author,
            'authors': [author] if author else [],
            'tags': [], 'pages': pages, 'update': update,
            'cover_url': cover_url,
            'cover': f'/api/cover?source=wnacg&id={aid}'
                     + (('&u=' + urllib.parse.quote(cover_url, safe='')) if cover_url else ''),
        })
    return out


WN_CATEGORIES = {
    '0': '', 'doujin': '5', 'single': '6', 'short': '9', 'cosplay': '3',
    'cg': '2', '3D': '22', 'ai': '37', 'japanese': '12', 'chinese': '1',
    'english': '16', 'magazine': '13', 'other': '10',
}
WN_RANK_TYPES = {
    'hot': '/albums-favorite_ranking.html',
    'day': '/albums-favorite_ranking.html',
    'week': '/albums-favorite_ranking.html',
    'month': '/albums-favorite_ranking.html',
    'latest': '/albums.html',
}


def wnacg_search(kw, page=1, proxy='', order='', time_range='', category='0', sub_category=''):
    """关键词搜索：/search/?q=...&p=页码"""
    page = max(1, min(int(page or 1), 200))
    cat = WN_CATEGORIES.get(str(category), '')
    params = {'q': kw, 'f': '_all', 's': 'create_time_DESC', 'syn': 'yes', 'p': str(page)}
    if cat:
        params['cate'] = cat
    url = _wn_url('/search/?' + urllib.parse.urlencode(params))
    doc = http_get(url, proxy=proxy)
    results = _wn_cards(doc)
    # wnacg 搜索结果一页固定 24 条，有下一页就报一个大总数，前端才能翻页
    has_next = bool(re.search(r'class="next"', doc))
    total = page * 24 + (24 if has_next else 0)
    return {'results': results, 'total': total, 'page': page, 'is_id': False,
            'source': 'wnacg', 'hasNext': has_next}


def wnacg_listing(page=1, proxy='', category='0', mode='latest'):
    """列表/排行：/albums-index-page-N.html 或按分类。"""
    page = max(1, min(int(page or 1), 200))
    cat = WN_CATEGORIES.get(str(category), '')
    if cat:
        path = f'/albums-index-cate-{cat}.html'
        if page > 1:
            path = f'/albums-index-cate-{cat}-page-{page}.html'
    else:
        path = '/albums.html' if page == 1 else f'/albums-index-page-{page}.html'
    doc = http_get(_wn_url(path), proxy=proxy)
    results = _wn_cards(doc)
    total = page * 20 + (20 if len(results) >= 20 else 0)
    return {'results': results, 'total': total, 'page': page, 'is_id': False,
            'source': 'wnacg', 'hasNext': len(results) >= 20}


def wnacg_ranking(mode='hot', page=1, proxy='', category='0'):
    if mode in ('day', 'week', 'month', 'hot'):
        doc = http_get(_wn_url('/albums-favorite_ranking.html'), proxy=proxy)
        results = _wn_cards(doc)
        # 排行榜页面只有一页，本地翻页
        per = 24
        start = (int(page) - 1) * per
        paged = results[start:start + per]
        return {'results': paged, 'total': len(results), 'page': int(page),
                'is_id': False, 'source': 'wnacg',
                'hasNext': start + per < len(results)}
    return wnacg_listing(page, proxy, category, 'latest')


def wnacg_author(author, page=1, proxy='', **kw):
    """绅士漫画无作者字段，退化为标题搜索（作者名多在标题 [] 内）。"""
    d = wnacg_search(author, page, proxy, **kw)
    d['author'] = author
    return d


def wnacg_detail(aid, proxy=''):
    """详情：标题、作者、标签、章节列表。"""
    doc = http_get(_wn_url(f'/photos-index-aid-{aid}.html'), proxy=proxy)
    title_m = re.search(r'<title>(.*?)\s*-\s*紳士漫畫', doc, re.S)
    title = clean_text(title_m.group(1)) if title_m else f'WN{aid}'
    # 章节：<a class="tagshow" data-chid="288388" href="/photos-slide-aid-288388-sid-407085.html" title="...25P...">
    chapters = []
    for m in re.finditer(r'<a class="tagshow"[^>]*href="/photos-slide-aid-(\d+)-sid-(\d+)\.html"[^>]*title="([^"]*)"', doc):
        sid, parent, ttl = m.group(1), m.group(2), clean_text(m.group(3))
        pcount = int((re.search(r'(\d+)P', ttl) or [None, '0'])[1] or 0)
        chapters.append({'sid': sid, 'title': ttl, 'pages': pcount})
    if not chapters:
        # 单章本子：/photos-slide-aid-{aid}.html，sid 就是 aid 自身
        single = re.search(r'id="reader-btn"[^>]*href="/photos-slide-aid-(\d+)\.html"', doc)
        if not single:
            single = re.search(r'/photos-item-aid-(\d+)\.html', doc)
        if single:
            chapters = [{'sid': single.group(1), 'title': title, 'pages': 0}]
        else:
            for m in re.finditer(r'/photos-slide-aid-(\d+)-sid-(\d+)\.html', doc):
                if m.group(1) not in [c['sid'] for c in chapters]:
                    chapters.append({'sid': m.group(1), 'title': '', 'pages': 0})
    tags = [clean_text(t) for t in re.findall(r'class="tagshow"[^>]*>([^<]+)</a>', doc)]
    cover = re.search(r'<img alt="[^"]*" src="(//[^"]+\.(?:webp|jpg|png))"', doc)
    cover_url = ('https:' + cover.group(1)) if cover else ''
    author = ''
    am = re.match(r'^\[([^\]]{1,40})\]', title)
    if am:
        author = am.group(1).strip()
    return {'id': str(aid), 'title': title, 'author': author,
            'authors': [author] if author else [],
            'tags': [t for t in tags if t][:12],
            'chapters': chapters, 'cover_url': cover_url,
            'pages': sum(c['pages'] for c in chapters)}


def wnacg_chapter_images(sid, proxy=''):
    """章节图片直链：/photos-item-aid-{sid}.html -> mReader.initData({page_url:[...]})"""
    doc = http_get(_wn_url(f'/photos-item-aid-{sid}.html'), proxy=proxy, timeout=60)
    block = re.search(r'"page_url"\s*:\s*\[(.*?)\]', doc, re.S)
    if not block:
        return []
    urls = re.findall(r'"(https?://[^"]+)"', block.group(1))
    return [u.replace('http://', 'https://') for u in urls]


def wnacg_all_images(aid, proxy='', log=None):
    """取整个本子的所有图片直链，按章节顺序、页内顺序。"""
    detail = wnacg_detail(aid, proxy=proxy)
    out = []
    for ch in detail['chapters']:
        urls = wnacg_chapter_images(ch['sid'], proxy=proxy)
        if log:
            log(f"章节 {ch['sid']}：{len(urls)} 张")
        for idx, u in enumerate(urls, 1):
            out.append({'url': u, 'name': f'{int(ch["sid"]):06d}-{idx:03d}',
                        'chapter': ch['sid'], 'index': idx})
    return out, detail


def wnacg_cover(cover_url, proxy=''):
    if not cover_url:
        return None
    return http_get(cover_url, proxy=proxy, timeout=25, retries=2, raw=True)


# ================================================================
# pica（哔咔漫画）：签名 API，需要账号
# ================================================================
PICA_HOSTS = ['https://picaapi.picacomic.com', 'https://picaapi.go2778.com']
PICA_API_KEY = 'C69BAF41DA5ABD1FFEDC6D2FEA56B'
PICA_HMAC_KEY = '~d}$Q7$eIni=V)9\\RK/P.RM4;9[7|@/CA}b~OW!3?EV`:<>M7pddUBL5n|0/*Cn'
PICA_APP_VERSION = '2.3.1.20241111'
PICA_BUILD = '20250144'
PICA_UA = 'okhttp/3.8.1'
_pica_token = ''
_pica_token_exp = 0
_pica_lock = __import__('threading').Lock()


def _pica_sign(path_and_query, method, ts, nonce):
    raw = (path_and_query.lstrip('/') + str(ts) + nonce + method.lower() + PICA_API_KEY).lower()
    return hmac.new(PICA_HMAC_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _pica_headers(pq, method, token=None):
    ts = int(time.time())
    nonce = ''.join(random.choices(string.ascii_lowercase + string.digits, k=32))
    # 哔咔签名覆盖 path + query（实测含 query 才有 data）
    h = {
        'accept': 'application/vnd.picacomic.com.v1+json',
        'time': str(ts), 'nonce': nonce,
        'signature': _pica_sign(pq, method, ts, nonce),
        'api-key': PICA_API_KEY,
        'app-channel': '1', 'app-version': PICA_APP_VERSION,
        'app-uuid': uuid.uuid4().hex, 'app-platform': 'android',
        'app-build-version': PICA_BUILD, 'image-quality': 'medium',
        'User-Agent': PICA_UA,
    }
    if token:
        h['authorization'] = token
    return h


def pica_request(method, pq, body=None, proxy='', token=None, hosts=None):
    """返回 data 字段；业务错误（data 非 dict）抛异常。"""
    global _pica_token
    hosts = hosts or PICA_HOSTS
    last = None
    for host in hosts:
        url = host.rstrip('/') + pq
        h = _pica_headers(pq, method, token)
        try:
            if method.upper() == 'POST':
                txt = http_post(url, body, proxy=proxy, headers=h, timeout=30, retries=2)
            else:
                txt = http_get(url, proxy=proxy, headers=h, timeout=30, retries=2)
            env = json.loads(txt)
            code = env.get('code', 200)
            data = env.get('data')
            if str(env.get('error') or '') == '1004':
                raise RuntimeError('哔咔账号或密码错误')
            if str(code) not in ('200', '0') or not isinstance(data, dict):
                raise RuntimeError(env.get('errorMsg') or env.get('message') or '响应无数据')
            return data
        except Exception as e:
            last = e
            continue
    raise last


def pica_login(account, password, proxy=''):
    """登录并缓存 token（内存）。"""
    global _pica_token, _pica_token_exp
    with _pica_lock:
        if _pica_token and _pica_token_exp > time.time():
            return _pica_token
        data = pica_request('POST', '/auth/sign-in',
                            {'email': account, 'password': password}, proxy=proxy)
        token = data.get('token') or ''
        if not token:
            raise RuntimeError('登录响应缺少 token')
        _pica_token = token
        _pica_token_exp = time.time() + 6 * 3600
        return token


def _pica_cover(item, proxy=''):
    thumb = item.get('thumb') or {}
    path = thumb.get('path') or ''
    server = thumb.get('fileServer') or ''
    if not path:
        return ''
    if path.startswith('http'):
        return path
    if not server:
        return path
    # 哔咔图片统一走 fileServer + /static/ + path（path 形如 tobeimg/xxx.jpg）
    return server.rstrip('/') + '/static/' + path.lstrip('/')


def _pica_map(item):
    author = ''
    for a in (item.get('author') or []):
        if isinstance(a, dict):
            author = author or (a.get('name') or a.get('local') or '')
        elif isinstance(a, str):
            author = author or a
    title = item.get('title') or ''
    aid = str(item.get('_id') or item.get('id') or '')
    cover_url = _pica_cover(item, '')
    return {
        'id': aid, 'title': title, 'author': author,
        'authors': [author] if author else [],
        'tags': [t for t in (item.get('tags') or []) if isinstance(t, str)][:8],
        'pages': int(item.get('pagesCount') or 0),
        'update': str(item.get('updatedAt') or '')[:10],
        'cover_url': cover_url,
        'cover': f'/api/cover?source=pica&id={aid}'
                 + (('&u=' + urllib.parse.quote(cover_url, safe='')) if cover_url else ''),
    }


def pica_search(kw, page=1, proxy='', account='', password='', **kw2):
    token = pica_login(account, password, proxy)
    body = {'keywords': kw, 'categories': [], 'sort': 'ddf'}
    data = pica_request('POST', f'/comics/advanced-search?page={page}', body,
                        proxy=proxy, token=token)
    docs = (data.get('comics') or {}).get('docs') or []
    return {'results': [_pica_map(x) for x in docs],
            'total': int((data.get('comics') or {}).get('total') or len(docs)),
            'page': page, 'is_id': False, 'source': 'pica',
            'hasNext': bool((data.get('comics') or {}).get('total', 0) > page * len(docs))}


def pica_categories(proxy='', account='', password=''):
    token = pica_login(account, password, proxy)
    data = pica_request('GET', '/categories', proxy=proxy, token=token)
    return [{'value': str(c.get('title') or ''), 'label': c.get('title') or ''}
            for c in (data.get('categories') or []) if c.get('active', True) and not c.get('isWeb')]


def pica_ranking(mode='hot', page=1, proxy='', account='', password='', category='0', **kw):
    token = pica_login(account, password, proxy)
    # /comics 只接受 page，sort/category 会返回 400；分类筛选走 advanced-search
    if category and str(category) != '0':
        data = pica_request('POST', f'/comics/advanced-search?page={page}',
                            {'keywords': '', 'categories': [str(category)], 'sort': 'ddf'},
                            proxy=proxy, token=token)
    else:
        data = pica_request('GET', f'/comics?page={page}', proxy=proxy, token=token)
    docs = (data.get('comics') or {}).get('docs') or []
    total = int((data.get('comics') or {}).get('total') or len(docs))
    return {'results': [_pica_map(x) for x in docs], 'total': total,
            'page': page, 'is_id': False, 'source': 'pica',
            'hasNext': total > page * max(1, len(docs))}


def pica_author(author, page=1, proxy='', account='', password='', **kw):
    return pica_search(author, page, proxy, account, password)


def pica_detail(aid, proxy='', account='', password=''):
    token = pica_login(account, password, proxy)
    data = pica_request('GET', f'/comics/{aid}', proxy=proxy, token=token)
    comic = data.get('comic') or {}
    # 章节列表是独立接口 /comics/{id}/eps，不在 comic 对象内
    ep_data = pica_request('GET', f'/comics/{aid}/eps', proxy=proxy, token=token)
    eps = ((ep_data.get('eps') or {}).get('docs') or [])
    chapters = []
    for i, e in enumerate(eps, 1):
        eid = str(e.get('_id') or e.get('id') or '')
        if not eid:
            continue
        # /order/{order}/pages 用的是章节序号（order 字段），不是章节 _id
        chapters.append({'sid': eid, 'order': int(e.get('order') or i),
                         'title': e.get('title') or '', 'pages': 0})
    chapters.sort(key=lambda c: c['order'])
    return {'id': aid, 'title': comic.get('title') or f'PICA{aid}',
            'author': '', 'authors': [], 'tags': comic.get('tags') or [],
            'cover_url': _pica_cover(comic, proxy),
            'chapters': chapters,
            'pages': int(comic.get('pagesCount') or 0)}


def pica_all_images(aid, proxy='', account='', password='', log=None):
    token = pica_login(account, password, proxy)
    detail = pica_detail(aid, proxy, account, password)
    out = []
    seq = 0
    for ch in detail['chapters']:
        # /comics/{id}/order/{order}/pages 里 order 是章节序号（数字），不是章节 _id
        order = int(ch.get('order') or 1)
        page = 1
        while True:
            data = pica_request('GET',
                f'/comics/{aid}/order/{order}/pages?page={page}',
                proxy=proxy, token=token)
            pages = data.get('pages') or {}
            docs = pages.get('docs') or []
            for i, p in enumerate(docs, 1):
                media = p.get('media') or {}
                path = media.get('path') or p.get('path') or ''
                server = media.get('fileServer') or p.get('fileServer') or ''
                if not path:
                    continue
                if path.startswith('http'):
                    url = path
                elif server:
                    url = server.rstrip('/') + '/static/' + path.lstrip('/')
                else:
                    url = path
                seq += 1
                out.append({'url': url,
                            'name': f'{order:02d}-{page:02d}-{i:03d}',
                            'chapter': ch['sid'], 'index': seq})
            if log:
                log(f'章节 {order}（{ch["title"] or ch["sid"]}）第 {page} 页：{len(docs)} 张')
            cur = int(pages.get('page') or page)
            total_pages = int(pages.get('pages') or 1)
            if not docs or cur >= total_pages:
                break
            page += 1
    return out, detail


def pica_cover(url, proxy=''):
    if not url:
        return None
    return http_get(url, proxy=proxy, timeout=25, retries=2, raw=True)


# ================================================================
# 统一入口
# ================================================================
SOURCES = ['jm', 'wnacg', 'pica']
SOURCE_LABELS = {'jm': '禁漫天堂', 'wnacg': '绅士漫画', 'pica': '哔咔漫画'}


def get_source_config(cfg):
    return {
        'proxy': (cfg.get('source_proxy') or '').strip() or (cfg.get('jm_proxy') or '').strip(),
        'pica_account': (cfg.get('pica_account') or '').strip(),
        'pica_password': (cfg.get('pica_password') or '').strip(),
    }


def source_search(source, kw, page, cfg, order='', time_range='', category='0', sub_category=''):
    sc = get_source_config(cfg)
    if source == 'wnacg':
        return wnacg_search(kw, page, sc['proxy'], order, time_range, category, sub_category)
    if source == 'pica':
        return pica_search(kw, page, sc['proxy'], sc['pica_account'], sc['pica_password'])
    raise RuntimeError('未知来源: ' + str(source))


def source_ranking(source, mode, page, cfg, category='0', **kw):
    sc = get_source_config(cfg)
    if source == 'wnacg':
        return wnacg_ranking(mode, page, sc['proxy'], category)
    if source == 'pica':
        return pica_ranking(mode, page, sc['proxy'], sc['pica_account'], sc['pica_password'], category)
    raise RuntimeError('未知来源: ' + str(source))


def source_author(source, author, page, cfg, **kw):
    sc = get_source_config(cfg)
    if source == 'wnacg':
        return wnacg_author(author, page, sc['proxy'], **kw)
    if source == 'pica':
        return pica_author(author, page, sc['proxy'], sc['pica_account'], sc['pica_password'])
    raise RuntimeError('未知来源: ' + str(source))


def source_cover(source, aid, cfg, direct_url=''):
    sc = get_source_config(cfg)
    if source == 'wnacg':
        return wnacg_cover(direct_url, sc['proxy'])
    if source == 'pica':
        return pica_cover(direct_url, sc['proxy'])
    raise RuntimeError('未知来源: ' + str(source))


def source_detail(source, aid, cfg, log=None):
    sc = get_source_config(cfg)
    if source == 'wnacg':
        return wnacg_detail(aid, sc['proxy'])
    if source == 'pica':
        return pica_detail(aid, sc['proxy'], sc['pica_account'], sc['pica_password'])
    raise RuntimeError('未知来源: ' + str(source))


def source_all_images(source, aid, cfg, log=None):
    sc = get_source_config(cfg)
    if source == 'wnacg':
        return wnacg_all_images(aid, sc['proxy'], log)
    if source == 'pica':
        return pica_all_images(aid, sc['proxy'], sc['pica_account'], sc['pica_password'], log)
    raise RuntimeError('未知来源: ' + str(source))


def source_tag(source):
    return {'wnacg': '绅士漫画', 'pica': '哔咔漫画'}.get(source, '禁漫')
