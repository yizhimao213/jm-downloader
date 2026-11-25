#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验证：jmcomic 下载 -> 上传本地影像馆 -> D1 入库 + 前台不展示"""
import json, time, urllib.request, urllib.error, sys, os

BASE = 'http://127.0.0.1:8899'
MEDIA = 'http://127.0.0.1:8790'
PW_FILE = r'D:/claude工作文件夹/blog/my-blog/my-blog/media/api/.dev.vars'


def req(url, data=None, method=None, token=None):
    headers = {}
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = 'Bearer ' + token
    m = method or ('POST' if data is not None else 'GET')
    r = urllib.request.Request(url, data=body, headers=headers, method=m)
    with urllib.request.urlopen(r, timeout=60) as resp:
        out = resp.read().decode('utf-8', 'ignore')
        return json.loads(out) if out else {}


def main():
    pw = ''
    for line in open(PW_FILE, encoding='utf-8'):
        if line.startswith('ADMIN_PASSWORD='):
            pw = line.split('=', 1)[1].strip()
            break
    if not pw:
        print('缺少本地密码'); sys.exit(1)

    # 1. 注入配置（本地影像馆 + 密码，只存运行内存）
    req(BASE + '/api/config', {
        'media_api': MEDIA, 'media_site': 'http://localhost:8780',
        'target_folder': 'JM验证', 'create_album': True,
        'media_password': pw,
    })
    print('[1] 配置已指向本地影像馆')

    # 2. 登录拿 token（验证写入前的基数）
    login = req(MEDIA + '/api/admin/login', {'password': pw})
    token = login['token']
    before = req(MEDIA + '/api/admin/items', token=token)
    before_n = len(before) if isinstance(before, list) else before.get('total', 0)
    print(f'[2] 登录成功，写入前 items 数约 {before_n}')

    # 3. 提交下载任务
    r = req(BASE + '/api/download', {'ids': ['1492816']})
    jid = r['jobs'][0]['id']
    print(f'[3] 任务已提交 {jid}')

    # 4. 轮询到终态
    deadline = time.time() + 240
    job = {}
    while time.time() < deadline:
        job = req(f'{BASE}/api/job?id={jid}')
        if job.get('status') in ('done', 'error'):
            break
        print(f"    进度 {job.get('phase')} {job.get('done')}/{job.get('total')}")
        time.sleep(4)
    print(f"[4] 终态: {job.get('status')} | 入库 {job.get('item_count')} 张")
    if job.get('error'):
        print('    错误:', job['error'][:200])
    for l in job.get('logs', [])[-6:]:
        print('    ', l)
    if job.get('status') != 'done':
        sys.exit(2)

    # 5. 验证 D1 里确实写入了 hidden + 不在画廊的记录
    items = req(MEDIA + '/api/admin/items', token=token)
    rows = items if isinstance(items, list) else items.get('list', items.get('items', []))
    jm_items = [x for x in rows if 'JM1492816' in str(x.get('title', ''))
                or '1492816' in str(x.get('tags', ''))
                or x.get('folder') == 'JM验证']
    print(f'[5] 命中验证记录 {len(jm_items)} 条')
    if jm_items:
        s = jm_items[0]
        print('    示例:', {k: s.get(k) for k in ('id', 'title', 'folder', 'hidden', 'inGallery', 'in_gallery', 'key')})

    # 6. 相册验证
    albums = req(MEDIA + '/api/admin/albums', token=token)
    al = albums if isinstance(albums, list) else albums.get('list', [])
    ja = [a for a in al if 'JM1492816' in str(a.get('title', ''))]
    if ja:
        print('    相册:', {k: ja[0].get(k) for k in ('id', 'title', 'hidden')})


if __name__ == '__main__':
    main()
