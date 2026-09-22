#!/usr/bin/env python3
"""QA Lab macOS login/status/import helper; Python 3.9+, standard library only."""
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

DEFAULT_ORIGIN = "https://qalab.claw.qihoo.net"


def normalize_origin(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError()
        host = parts.hostname.lower()
        if ":" in host: host = "[" + host + "]"
        port = parts.port
        return "https://" + host + (":" + str(port) if port and port != 443 else "")
    except ValueError:
        raise RuntimeError("平台地址必须是 HTTPS 源地址，不带路径、用户名、密码或查询参数") from None


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


def validate_payload(payload):
    cases = payload.get("cases", [])
    if not isinstance(cases, list) or not cases or len(cases) > 20:
        raise RuntimeError("导入包须包含 1–20 条真实执行通过的用例")
    if len({c.get("title") for c in cases}) != len(cases):
        raise RuntimeError("同批用例标题须唯一")
    for case in cases:
        script, report = case.get("script"), case.get("report")
        if case.get("verdict") != "pass" or not isinstance(script, list) or not script or not isinstance(report, list) or len(script) != len(report):
            raise RuntimeError("只导入执行通过且逐步报告完整的用例")
        if not any(str(step.get("action", "")).startswith("assert") or step.get("action") == "judge" for step in script):
            raise RuntimeError("用例必须包含业务断言")
        for step, result in zip(script, report):
            if step.get("action") != result.get("action") or result.get("ok") is not True:
                raise RuntimeError("报告须与完整脚本逐步对应且全部通过")
            check = result.get("check")
            if str(step.get("action", "")).startswith("assert") and (not isinstance(check, dict) or not {"actual", "expected"}.issubset(check)):
                raise RuntimeError("断言缺少实际值或预期值")
            if isinstance(check, dict) and check.get("pass") is False:
                raise RuntimeError("报告包含失败断言")


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

    def status(self, token):
        me = self.api("GET", "/api/auth/me", token=token)
        projects = self.api("GET", "/api/projects", token=token)
        devices = self.api("GET", "/api/devices", token=token)
        schema = self.api("GET", "/openapi.json")
        fields = ("id", "runner_id", "name", "platform", "active_kinds", "last_seen_at")
        return {"platform": self.api.origin, "user": me["user"]["username"], "projects": projects,
                "devices": [{k: d.get(k) for k in fields} for d in devices],
                "verified_import_available": "post" in schema.get("paths", {}).get("/api/verified-imports", {}),
                "dedup_available": "post" in schema.get("paths", {}).get("/api/verified-imports/preview", {})}

    def preview(self, payload, token):
        validate_payload(payload)
        return self.api("POST", "/api/verified-imports/preview", payload, token)

    def import_cases(self, payload, token):
        validate_payload(payload)
        status = self.status(token)
        if not status["verified_import_available"]:
            raise RuntimeError("线上缺少导入接口；保留待导入包，不会改写 localhost")
        if not status["dedup_available"]:
            raise RuntimeError("线上尚未发布平台查重接口；请先更新服务器，保留报告不盲目新增")
        if payload.get("project_id") not in {p["id"] for p in status["projects"]}:
            raise RuntimeError("无法访问导入包指定的线上项目")
        if payload.get("runner_device_id") not in {d["id"] for d in status["devices"]}:
            raise RuntimeError("须使用当前账号在同一线上平台登记的设备")
        receipt = self.api("POST", "/api/verified-imports", payload, token)
        try:
            records = receipt["records"]
            if len(records) != len(payload["cases"]) or receipt["project_id"] != payload["project_id"]:
                raise ValueError("count/project")
            expected = {c["title"]: c for c in payload["cases"]}
            seen = set()
            for record in records:
                title = record["title"]
                if title in seen: raise ValueError("duplicate")
                seen.add(title)
                source = expected[title]
                saved = self.api("GET", "/api/ai/testcases/" + str(record["case_id"]), token=token)
                if saved.get("project_id") != payload["project_id"]:
                    raise ValueError("project")
                if record.get("disposition") in ("created", "reused"):
                    run = self.api("GET", "/api/exec-queue/" + str(record["run_id"]), token=token)
                    snapshot = run["payload"]
                    if isinstance(snapshot, str): snapshot = json.loads(snapshot)
                    if (run.get("project_id") != payload["project_id"] or
                        run.get("test_case_id") != record["case_id"] or run.get("verdict") != "pass" or
                        any(snapshot.get(k) != source[k] for k in ("title", "steps", "expected")) or
                        normalize_script(snapshot["script"]) != normalize_script(source["script"]) or
                        run.get("report") != source["report"]):
                        raise ValueError("execution readback")
                elif (any(saved.get(k) != source[k] for k in ("title", "steps", "expected")) or
                      normalize_script(saved["script"]) != normalize_script(source["script"])):
                    raise ValueError("readback")
        except (KeyError, TypeError, ValueError, RuntimeError):
            raise RuntimeError("已提交，但读回复核未完成或不一致；保留原 external_id 和内容，勿重复新建，请检查线上记录") from None
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "status", "import", "logout", "doctor", "preview"))
    parser.add_argument("--base-url", default=DEFAULT_ORIGIN)
    parser.add_argument("--payload", type=Path)
    args = parser.parse_args(argv)
    origin = normalize_origin(args.base_url)
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
    if not args.payload: raise RuntimeError("import 需要 --payload <包.json>")
    payload = json.loads(args.payload.read_text(encoding="utf-8-sig"))
    if args.action == "preview":
        print(json.dumps(client.preview(payload, token), ensure_ascii=False, indent=2)); return
    receipt = client.import_cases(payload, token)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(origin + "/case-library?project_id=" + str(receipt["project_id"]))
    print(origin + "/exec-results?project_id=" + str(receipt["project_id"]) + "&batch_id=" + receipt["batch_id"])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyboardInterrupt, EOFError) as error:
        print("操作未完成：" + (str(error) if not isinstance(error, KeyboardInterrupt) else "用户取消"), file=sys.stderr)
        sys.exit(1)
