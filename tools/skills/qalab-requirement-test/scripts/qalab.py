#!/usr/bin/env python3
"""QA Lab macOS helper; Python 3.9+ stdlib, Node.js for script validation."""
import argparse
import getpass
import json
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from mac_keychain import MacKeychain
from script_targets import validate_targets

DEFAULT_ORIGIN = "https://qalab.claw.qihoo.net"


def normalize_origin(value):
    try:
        parts = urlsplit(value)
        local_http = parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")
        if ((parts.scheme != "https" and not local_http) or not parts.hostname or parts.username or parts.password
                or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError()
        host = parts.hostname.lower()
        if ":" in host: host = "[" + host + "]"
        port = parts.port
        default_port = 443 if parts.scheme == "https" else 80
        return parts.scheme + "://" + host + (":" + str(port) if port and port != default_port else "")
    except ValueError:
        raise RuntimeError("平台地址必须是 HTTPS 源地址；本机 localhost、127.0.0.1、[::1] 可用 HTTP；不带路径、用户名、密码或查询参数") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Api:
    def __init__(self, origin):
        self.origin = normalize_origin(origin)
        self.opener = build_opener(NoRedirect())

    def __call__(self, method, path, body=None, token=None):
        if not path.startswith("/") or path.startswith("//"):
            raise RuntimeError("无效 API 路径")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token: headers["Authorization"] = "Bearer " + token
        try:
            with self.opener.open(Request(self.origin + path, data=data, headers=headers, method=method), timeout=30) as response:
                payload = json.load(response)
        except HTTPError as error:
            if error.code == 409:
                try:
                    conflict = json.load(error).get("data", {})
                    if isinstance(conflict, dict) and conflict.get("reason") == "duplicate_confirmation_required":
                        raise RuntimeError("需确认疑似重复用例；未写入本批数据：\n" + json.dumps(conflict, ensure_ascii=False, indent=2))
                except (ValueError, TypeError, AttributeError):
                    pass
            hint = "请重新 login" if error.code == 401 else "请检查权限、接口及数据；不要重复创建新 external_id"
            raise RuntimeError(f"平台请求失败：HTTP {error.code}，{hint}") from None
        except (URLError, TimeoutError, OSError):
            raise RuntimeError("无法连接平台；请检查网络、VPN 和证书，未关闭 HTTPS 校验") from None
        except (ValueError, UnicodeError):
            raise RuntimeError("平台未返回有效 JSON") from None
        if not isinstance(payload, dict):
            raise RuntimeError("平台响应格式不正确")
        if payload.get("code", 0) != 0:
            raise RuntimeError(f"平台拒绝请求（code={payload.get('code')}）；请检查权限与参数")
        return payload.get("data", payload)


def validate_payload(payload, draft=False):
    """Use the same offline action/evidence contract on Windows and macOS."""
    import subprocess
    try:
        result = subprocess.run(['node', str(Path(__file__).with_name('validate-contract.mjs'))] + (['--draft'] if draft else []),
                                input=json.dumps(payload, ensure_ascii=False), text=True, encoding='utf-8', capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('离线协议校验需要可用 Node.js；无需安装 Runner 或配置设备 token') from error
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or '脚本/实测证据校验失败')


def delivery_check(action, manifest, packet, platform, receipt=None):
    import subprocess
    args = ['node', str(Path(__file__).with_name('delivery.mjs')), action, '--manifest', str(manifest), '--packet', str(packet), '--platform', platform]
    if receipt: args += ['--receipt', str(receipt)]
    result = subprocess.run(args, text=True, encoding='utf-8', capture_output=True, timeout=30)
    if result.returncode: raise RuntimeError(result.stderr.strip() or '场景清单校验失败')


def normalize_script(script):
    if isinstance(script, str): script = json.loads(script)
    return [{**step, "target": step.get("target") or {}, "args": step.get("args") or {}} for step in script]


class Client:
    def __init__(self, api, store):
        self.api, self.store = api, store

    def login(self, username, password):
        session = self.api("POST", "/api/auth/login", {"username": username, "password": password})
        self.store.save(session)
        return session["access_token"]

    def token(self):
        session = self.store.load()
        if not isinstance(session, dict) or not session.get("access_token"):
            raise RuntimeError("请先执行 login，在终端输入自己的线上账号密码")
        if session.get("refresh_token"):
            fresh = self.api("POST", "/api/auth/refresh", {"refresh_token": session["refresh_token"]})
            if not fresh.get("access_token"): raise RuntimeError("刷新响应缺少 access_token")
            session.update({k: fresh[k] for k in ("access_token", "refresh_token") if fresh.get(k)})
            self.store.save(session)
        return session["access_token"]

    def status(self, token, include_devices=True):
        me = self.api("GET", "/api/auth/me", token=token)
        projects = self.api("GET", "/api/projects", token=token)
        devices = self.api("GET", "/api/devices", token=token) if include_devices else []
        schema = self.api("GET", "/openapi.json")
        fields = ("id", "runner_id", "name", "platform", "active_kinds", "last_seen_at")
        return {"platform": self.api.origin, "user": me["user"]["username"], "projects": projects,
                "devices": [{k: d.get(k) for k in fields} for d in devices],
                "verified_import_available": "post" in schema.get("paths", {}).get("/api/verified-imports", {}),
                "async_import_available": "post" in schema.get("paths", {}).get("/api/verified-imports/jobs", {}),
                "dedup_available": "post" in schema.get("paths", {}).get("/api/verified-imports/preview", {})}

    def preview(self, payload, token):
        validate_payload(payload)
        if payload.get('import_mode') == 'codex':
            raise RuntimeError('Codex 实测使用异步 import，不调用旧 preview')
        return self.api("POST", "/api/verified-imports/preview", payload, token)

    def import_cases(self, payload, token):
        validate_payload(payload)
        codex = payload.get('import_mode') == 'codex'
        if codex:
            caps = self.api('GET', '/api/verified-imports/capabilities', token=token)
            if 'codex' not in caps.get('import_modes', []) or caps.get('codex_evidence_protocol') != 'qalab-codex-v1':
                raise RuntimeError('平台尚未支持 Codex 实测导入；保留原包，更新平台后重试，不要求本机安装 Runner')
        from script_targets import NETWORK_ACTIONS
        required = {s.get('action') for c in payload['cases'] for s in c['script']} & NETWORK_ACTIONS
        if required:
            try:
                caps = self.api('GET', '/api/verified-imports/capabilities', token=token)
            except RuntimeError as exc:
                raise RuntimeError('目标平台尚未确认网络场景协议；请更新平台，保留原始实测包，不提交或改投其他环境') from exc
            if 'network-scenarios-v1' not in caps.get('replay_protocols', []) or not required.issubset(set(caps.get('actions', []))):
                raise RuntimeError('目标平台不支持本批脚本的网络场景动作；请更新平台后重试原包')
        status = self.status(token, include_devices=False) if codex else self.status(token)
        if not status["async_import_available"]:
            raise RuntimeError("线上尚未发布异步导入接口；保留待导入包，请先更新服务器，不退回同步导入")
        if payload.get("project_id") not in {p["id"] for p in status["projects"]}:
            raise RuntimeError("无法访问导入包指定的线上项目")
        if not codex and payload.get("runner_device_id") not in {d["id"] for d in status["devices"]}:
            raise RuntimeError("须使用当前账号在同一线上平台登记的设备")
        receipt = self.api("POST", "/api/verified-imports/jobs", payload, token)
        if (receipt.get("accepted") is not True or type(receipt.get("job_id")) is not int or receipt["job_id"] <= 0 or
            receipt.get("project_id") != payload["project_id"] or receipt.get("external_id") != payload["external_id"] or
            receipt.get("case_count") != len(payload["cases"])):
            raise RuntimeError("提交回执无法确认；保留原 external_id 和内容重试，勿生成新 ID；不宣称已经入库")
        # Acceptance is the end of this task: NEVER poll or wait for deduplication.
        return receipt

    def job(self, job_id, token):
        if not isinstance(job_id, int) or job_id <= 0:
            raise RuntimeError("job 需要正整数 --job-id")
        return self.api("GET", f"/api/verified-imports/jobs/{job_id}", token=token)



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "status", "import", "logout", "doctor", "preview", "job", "validate", "lint"))
    parser.add_argument("--base-url", default=DEFAULT_ORIGIN)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--job-id", type=int)
    parser.add_argument("--receipt-path", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    if args.action in ("lint", "validate", "import", "preview"):
        if not args.payload: raise RuntimeError("需要 --payload <包.json>")
        validate_payload(json.loads(args.payload.read_text(encoding="utf-8-sig")), draft=args.action == "lint")
        if args.action in ("lint", "validate"):
            print("本地导入结构校验通过；未连接平台，不代表设备实测通过。")
            return
    origin = normalize_origin(args.base_url)
    if args.action == "import" and args.manifest:
        delivery_check("ready", args.manifest, args.payload, origin)
    if args.action == "doctor":
        store = MacKeychain(origin, suffix=":self-test-" + uuid.uuid4().hex)
        sample = {"access_token": "keychain-self-test-not-a-real-token"}
        try:
            store.save(sample)
            if store.load() != sample: raise RuntimeError("钥匙串读写不一致")
        finally:
            store.delete()
        if store.load() is not None: raise RuntimeError("钥匙串自检清理失败")
        print("macOS 钥匙串读写和清理自检通过；未连接平台。")
        return
    store = MacKeychain(origin)
    if args.action == "logout":
        store.delete(); print("已删除该平台在本机钥匙串中的会话。"); return
    client = Client(Api(origin), store)
    if args.action == "login":
        if not sys.stdin.isatty(): raise RuntimeError("请在自己的交互式终端运行 login")
        username = input("线上用户名：").strip()
        password = getpass.getpass("线上密码（隐藏输入）：")
        try: token = client.login(username, password)
        finally: password = None
        print("已安全登录：" + origin)
    else:
        token = client.token()
    if args.action in ("login", "status"):
        print(json.dumps(client.status(token), ensure_ascii=False, indent=2)); return
    if args.action == "job":
        print(json.dumps(client.job(args.job_id, token), ensure_ascii=False, indent=2)); return
    if not args.payload: raise RuntimeError("import 需要 --payload <包.json>")
    payload = json.loads(args.payload.read_text(encoding="utf-8-sig"))
    if args.action == "preview":
        print(json.dumps(client.preview(payload, token), ensure_ascii=False, indent=2)); return
    receipt = client.import_cases(payload, token)
    receipt_path = args.receipt_path or (args.manifest.parent / ("receipt-" + str(receipt['job_id']) + ".json") if args.manifest else None)
    try:
        if receipt_path: receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.manifest: delivery_check("submitted", args.manifest, args.payload, origin, receipt_path)
    except (RuntimeError, OSError) as error:
        print("平台已接收；本地回执/清单保存失败，保留下方回执，不要重新执行用例：" + str(error), file=sys.stderr)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    print("测试结果已提交，平台正在后台整理；无需等待。此回执不代表用例已入库。")
    print(origin + "/verified-imports?project_id=" + str(receipt["project_id"]) + "&job_id=" + str(receipt["job_id"]))



if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyboardInterrupt, EOFError) as error:
        print("操作未完成：" + (str(error) if not isinstance(error, KeyboardInterrupt) else "用户取消"), file=sys.stderr)
        sys.exit(1)
