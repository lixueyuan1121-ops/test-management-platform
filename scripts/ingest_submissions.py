#!/usr/bin/env python3
"""供云端转发自动化经 nbot 在 Mac 上调用；只录入提测，不触碰旧派单看板。

python3 scripts/ingest_submissions.py --input /tmp/qa-ingest-batch.json
复用 qa-dispatch-agent/config/platform.json 的现有账号；不会输出账号、密码或 token。
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_CONFIG = Path.home() / 'code/daily_test/qa-dispatch-agent/config/platform.json'


def request(base, path, token=None, body=None, timeout=10):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = Request(base.rstrip('/') + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                  headers=headers)
    try:
        with urlopen(req, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f'平台接口 {path} 返回 HTTP {exc.code}') from None
    except (URLError, TimeoutError, ValueError, OSError):
        raise RuntimeError(f'平台接口 {path} 网络异常或返回非 JSON') from None
    if not isinstance(result, dict):
        raise RuntimeError(f'平台接口 {path} 返回格式错误')
    if path == '/openapi.json':
        return result
    if result.get('code') != 0:
        raise RuntimeError(f'平台接口 {path} 未返回成功状态')
    return result.get('data')


def ingest_batch(config, entries, probe_only=False):
    base = config['baseUrl']
    schema = request(base, '/openapi.json')
    if 'post' not in schema.get('paths', {}).get('/api/tasks/ingest', {}):
        raise RuntimeError('平台尚未部署提测自动录入接口；保留待录入记录，下轮重试')
    token = request(base, '/api/auth/login', body={'username': config['username'], 'password': config['password']})['access_token']
    projects = request(base, '/api/projects', token=token)
    project = next((p for p in projects if p['code'] == config['projectCode']), None)
    if not project:
        raise RuntimeError('配置的项目不存在或账号无权访问')
    if probe_only:
        return {'ready': True, 'project_id': project['id'], 'project_name': project['name']}
    results = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('source_message_id'), str):
            results.append({'ok': False, 'source_message_id': None, 'error': '必须提供字符串类型的原消息 ID'})
            continue
        msgid = entry['source_message_id']
        try:
            # 仅用已配置的项目，输入不允许切换目的平台/项目或设置负责人。
            payload = dict(entry)
            payload['project_id'] = project['id']
            payload['source_group_id'] = '7652669649127481'
            data = request(base, '/api/tasks/ingest', token=token, body=payload)
            results.append({'ok': True, 'source_message_id': msgid, 'task_id': data['task']['id'], 'created': data['created']})
        except (RuntimeError, KeyError, TypeError) as exc:
            results.append({'ok': False, 'source_message_id': msgid, 'error': str(exc)})
    return {'results': results, 'ok': all(item['ok'] for item in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--base-url', help='覆盖旧平台地址；不修改现有账号配置')
    parser.add_argument('--probe', action='store_true', help='仅检查接口、登录和项目，不创建任务')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        if args.base_url:
            config['baseUrl'] = args.base_url
        entries = [] if args.probe else json.loads(args.input.read_text()) if args.input else None
        if not isinstance(entries, list):
            raise RuntimeError('--input 必须是提测对象组成的 JSON 数组')
        result = ingest_batch(config, entries, args.probe)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('ok', result.get('ready', False)) else 1
    except RuntimeError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        # 配置/HTTP 错误不回显凭据或原始响应；队列必须由调用者保留。
        print(json.dumps({'ok': False, 'error': '提测录入暂不可用，请检查平台接口版本、连接和已有登录配置；保留待录入记录'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
