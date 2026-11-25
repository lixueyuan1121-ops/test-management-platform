"""Run the private geelib-attachment CLI with the reporting user's SSO transport."""
import hashlib
import json
import os
import platform
import secrets
import subprocess
import tempfile
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

from app.core.config import settings
from app.services import md_bug_skill
from app.services.geelib import GeelibError

ENDPOINT = '/openapi/OpenApiDemandMgr/addAttachmentFile'
BINARIES = {
    ('Darwin', 'arm64'): ('attachment-darwin-arm64', '3728c0372591c9057c83ebf8917c17a51a06edd8fc4c81d2af768898b69b0d36'),
    ('Darwin', 'x86_64'): ('attachment-darwin-amd64', '3d86a0ac3b1b466cd084739a4d5b9a08f966d305a099459ae3ab1121f1fa4eea'),
    ('Linux', 'x86_64'): ('attachment-linux-amd64', '78f5560585f44654060bcb88a279a732f7a997108fc0afa72b64f8fe9ad43694'),
    ('Windows', 'amd64'): ('attachment-windows-amd64.exe', 'a007ff12edd7d52a73760a9debeae9d5593943ed6832354a36990023bece4f33'),
}


def upload(sub_id, matter_id, name, content, mime, token):
    binary = BINARIES.get((platform.system(), platform.machine().lower()))
    if not binary:
        raise GeelibError('附件技能不支持此服务器架构')
    cli = md_bug_skill.PACKAGE / 'geelib-attachment/cli/bin' / binary[0]
    if not cli.is_file() or hashlib.sha256(cli.read_bytes()).hexdigest() != binary[1]:
        raise GeelibError('服务器附件技能缺失或校验失败')
    # The supplied CLI signs requests with its own protocol. One private loopback endpoint
    # accepts exactly this file/target and replaces legacy signing fields with personal SSO.
    route = '/' + secrets.token_hex(24)
    fields = {'sub_id': str(sub_id), 'id': str(matter_id), 'type': 'matter'}
    sent = False
    response = None

    class Transport(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal sent, response
            if sent or self.path != route + ENDPOINT:
                return self.send_error(404)
            length = int(self.headers.get('Content-Length') or 0)
            if not 0 < length <= len(content) + 16384:
                return self.send_error(400)
            packet = BytesParser(policy=policy.default).parsebytes(
                ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode('ascii') + self.rfile.read(length))
            parts = {p.get_param('name', header='Content-Disposition'): p for p in packet.iter_parts()}
            file = parts.get('file')
            if (file is None or file.get_payload(decode=True) != content
                    or any(key not in parts or parts[key].get_content() != value for key, value in fields.items())):
                return self.send_error(400)
            sent = True
            try:
                upstream = requests.post(settings.GEELIB_API_URL.rstrip('/') + ENDPOINT,
                    headers={'X-Agent-Auth': 'Bearer ' + token}, data=fields,
                    files={'file': (name, file.get_payload(decode=True), mime)}, timeout=(5, 25), allow_redirects=False)
                if 200 <= upstream.status_code < 300:
                    response = upstream.json()
                reply = json.dumps(response or {'errno': -1}).encode()
                self.send_response(upstream.status_code)
                self.send_header('Content-Length', str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
            except (requests.RequestException, OSError, ValueError):
                self.close_connection = True

    with tempfile.TemporaryDirectory(prefix='qalab-attachment-') as folder, HTTPServer(('127.0.0.1', 0), Transport) as bridge:
        file = Path(folder) / ('evidence' + Path(name).suffix)
        file.write_bytes(content)
        worker = threading.Thread(target=bridge.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        env = {key: os.environ[key] for key in ('SYSTEMROOT', 'SystemRoot') if key in os.environ}
        env.update(HOME=folder, USERPROFILE=folder, JIKUYUN_API_BASE=f'http://127.0.0.1:{bridge.server_port}{route}',
                   JIKUYUN_USER_MAIL='qalab@localhost', JIKUYUN_TOKEN=secrets.token_hex(16))
        worker.start()
        try:
            subprocess.run([str(cli), 'upload', '--sub_id', str(sub_id), '--type', 'matter', '--id', str(matter_id),
                            '--file', str(file)], env=env, cwd=folder, capture_output=True, timeout=40)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            bridge.shutdown()
            worker.join()
    if not sent:
        raise GeelibError('附件技能未能发出上传请求，请检查服务账号的执行权限')
    if not isinstance(response, dict) or not isinstance(response.get('errno'), int):
        raise ValueError('附件上传回执不完整')
    if response['errno'] != 2000:
        raise GeelibError(str(response.get('errmsg') or '极库云拒绝上传附件')[:200])
    return response.get('data')
