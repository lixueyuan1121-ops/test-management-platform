"""Attachment import regressions; isolated SQLite and temporary upload directory."""
import csv
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts import test_eval_import as base
from app.api.eval_queue import _payload_of
from app.models import EvalQuery, User
from app.services.eval_import import parse_eval_template


def template(value, header='附件'):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['标题', '提问prompt', header])
    writer.writerow(['分析', '总结附件', value])
    return out.getvalue()


def main():
    base._seed()
    urls = ['https://cdn.example.com/%E8%B5%84%E6%96%99.pdf?sig=a,b', 'https://cdn.example.com/data.csv']
    for header in ['附件', '附件链接', 'attachments', 'attachment']:
        rows, skipped = parse_eval_template(template(';'.join(urls), header))
        assert not skipped
        assert rows[0]['attachments'] == [{'name': '资料.pdf', 'url': urls[0]}, {'name': 'data.csv', 'url': urls[1]}]
    values = [{'name': '自定义.pdf', 'url': urls[0]}, urls[1]]
    rows, skipped = parse_eval_template(template(json.dumps(values, ensure_ascii=False)))
    assert not skipped and rows[0]['attachments'][0]['name'] == '自定义.pdf'
    rows, skipped = parse_eval_template('标题\t提问\t附件\n分析\t总结附件\t' + ';'.join(urls))
    assert not skipped and len(rows[0]['attachments']) == 2
    for bad in ['missing.pdf', 'file:///tmp/a.pdf', 'javascript:alert(1)', '[broken', '{}', '[123]',
                '[{"url":"https://cdn.example.com/a","name":4}]', ';'.join([urls[0]] * 21),
                'https://cdn.example.com/a.pdf;https://other.example.com/a.pdf']:
        rows, skipped = parse_eval_template(template(bad))
        assert not rows and skipped[0]['line'] == 1, (bad, rows, skipped)
    rows, skipped = parse_eval_template('标题,提问\n旧模板,问题')
    assert not skipped and rows[0]['attachments'] == []

    uploads_app = next(route.app for route in base.app.routes if route.name == 'uploads')
    with tempfile.TemporaryDirectory() as directory, \
            patch('app.api.ai_eval._INPUT_ROOT', Path(directory) / 'eval_inputs'), \
            patch.object(uploads_app, 'all_directories', [directory]), \
            patch('app.api.ai_eval.settings.PLATFORM_BASE_URL', 'https://platform.example'):
        response = base.client.post('/api/ai/eval-queries/import-attachment', params={'project_id': 1},
                                    files={'file': ('资料.pdf', b'example attachment', 'application/pdf')})
        assert response.status_code == 200, response.text
        attachment = response.json()['data']
        stored = Path(directory) / 'eval_inputs' / '1' / attachment['url'].rsplit('/', 1)[-1]
        assert stored.read_bytes() == b'example attachment'
        assert stored.suffix == '.bin' and attachment['name'] == '资料.pdf'
        assert attachment['url'].startswith('https://platform.example/uploads/')
        downloaded = base.client.get(urlsplit(attachment['url']).path)
        assert downloaded.status_code == 200 and downloaded.content == b'example attachment'
        assert downloaded.headers['content-type'] == 'application/octet-stream'
        body = {'project_id': 1, 'text': template('资料.pdf;' + urls[1]),
                'uploaded_files': {'资料.pdf': attachment['url']}, 'dry_run': True}
        count = base._count_queries(1)
        preview = base.client.post('/api/ai/eval-queries/import', json=body).json()['data']
        assert preview['count'] == 1 and base._count_queries(1) == count
        expected = preview['preview'][0]['attachments']
        assert expected[0] == {'name': '资料.pdf', 'url': attachment['url']}
        body['dry_run'] = False
        result = base.client.post('/api/ai/eval-queries/import', json=body).json()['data']
        query = base._s.get(EvalQuery, result['queries'][0]['id'])
        assert json.loads(query.attachments) == expected
        assert _payload_of(query)['attachments'] == expected
        listed = base.client.get('/api/ai/eval-queries', params={'project_id': 1}).json()['data']
        assert listed[0]['attachments'] == expected
        with patch('app.api.ai_eval._MAX_INPUT_BYTES', 3):
            response = base.client.post('/api/ai/eval-queries/import-attachment', params={'project_id': 1},
                                        files={'file': ('large.pdf', b'1234')})
            assert response.status_code == 400
        assert len(list(Path(directory).rglob('*.bin'))) == 1
        response = base.client.post('/api/ai/eval-queries/import-attachment', params={'project_id': 999},
                                    files={'file': ('missing.pdf', b'x')})
        assert response.status_code == 404
        base._s.add(User(id=2, username='outsider', password_hash='x', name='无权限'))
        base._s.commit()
        with patch.dict(base.app.dependency_overrides, {base.get_current_user: lambda: base._s.get(User, 2)}):
            response = base.client.post('/api/ai/eval-queries/import-attachment', params={'project_id': 1},
                                        files={'file': ('denied.pdf', b'x')})
            assert response.status_code == 403
        assert len(list(Path(directory).rglob('*.bin'))) == 1
    with patch('app.api.ai_eval.extractors.extract_from_url', return_value=('表', template(urls[0]))):
        response = base.client.post('/api/ai/eval-queries/import', json={
            'project_id': 1, 'feishu_url': 'https://x.feishu.cn/sheets/abc', 'dry_run': True})
        assert response.json()['data']['preview'][0]['attachments'][0]['url'] == urls[0]
    print('PASS: URL/JSON/TSV/local attachments, invalid rows, upload bounds/permissions, preview, persistence, dispatch snapshot, Feishu')


if __name__ == '__main__':
    main()
