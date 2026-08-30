"""Nami 静态部署服务自测(mock 网络)。
运行: cd backend && .venv/bin/python -m scripts.test_nami_deploy

覆盖:
- deploy_html:上传取 base_url → 取短链成功返回短 URL
- 取短链失败 → 回落目录 base_url(仍公网可访问)
- 无 vm_id → 直接返回 base_url(不取短链)
- 上传失败(code!=0 / HTTP!=200)→ NamiDeployError(调用方回落自托管)
- 签名头用 platform.system()(Windows 无 os.uname() 也不崩)
- HTML 打包:index.html 在 zip 根层
"""
import io
import json
import zipfile

from app.services import nami_deploy as nd

# ── mock requests.post:按 URL 分派假响应 ──
_posts = []


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


_scenario = {"upload": None, "short": None}


def _fake_post(url, **kw):
    _posts.append((url, kw))
    if "upload" in url:
        return _scenario["upload"]
    return _scenario["short"]


nd.requests.post = _fake_post
# 绕开真实凭据读取
nd._read_cookie = lambda: "k=v"
nd._read_vm_id = lambda: "p-vm-123"


def test_html_zip_root_index():
    data = nd._html_to_zip_bytes("<p>hi</p>")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["index.html"], zf.namelist()
        assert zf.read("index.html") == b"<p>hi</p>"
    print("OK HTML 打包:index.html 在根层")


def test_signed_headers_windows_safe():
    # 不依赖 os.uname();platform.system() 各平台都可用
    h = nd._signed_headers(nd._UPLOAD_API_PATH, "k=v")
    assert h["zm-token"] and h["Cookie"] == "k=v" and h["nami-platform"]
    print("OK 签名头(platform.system,Windows 安全)")


def test_deploy_success_short():
    _scenario["upload"] = _Resp(200, {"code": 0, "data": {"base_url": "https://x.zhaomi.cn/aaa/"}})
    _scenario["short"] = _Resp(200, {"code": 0, "data": {"shor_url": "https://p8rxf.zhaomi.cn/"}})
    url = nd.deploy_html("<p>报告</p>")
    assert url == "https://p8rxf.zhaomi.cn/", url
    print("OK 上传+取短链成功 → 短 URL")


def test_short_fail_falls_back_baseurl():
    _scenario["upload"] = _Resp(200, {"code": 0, "data": {"base_url": "https://x.zhaomi.cn/bbb/"}})
    _scenario["short"] = _Resp(500, {"msg": "boom"})
    url = nd.deploy_html("<p>报告</p>")
    assert url == "https://x.zhaomi.cn/bbb/", url  # 取短链失败退回目录 URL
    print("OK 取短链失败 → 回落目录 base_url")


def test_no_vmid_returns_baseurl():
    orig = nd._read_vm_id
    nd._read_vm_id = lambda: ""
    try:
        _scenario["upload"] = _Resp(200, {"code": 0, "data": {"base_url": "https://x.zhaomi.cn/ccc/"}})
        url = nd.deploy_html("<p>报告</p>")
        assert url == "https://x.zhaomi.cn/ccc/", url
    finally:
        nd._read_vm_id = orig
    print("OK 无 vm_id → 直接返回 base_url")


def test_upload_fail_raises():
    _scenario["upload"] = _Resp(200, {"code": -1, "msg": "cookie 过期"})
    try:
        nd.deploy_html("<p>报告</p>")
        assert False, "上传失败应抛 NamiDeployError"
    except nd.NamiDeployError as e:
        assert "code=-1" in str(e), str(e)
    print("OK 上传失败 → NamiDeployError(调用方回落自托管)")


def test_upload_http_error_raises():
    _scenario["upload"] = _Resp(401, {"msg": "unauthorized"})
    try:
        nd.deploy_html("<p>报告</p>")
        assert False, "HTTP 401 应抛 NamiDeployError"
    except nd.NamiDeployError as e:
        assert "401" in str(e), str(e)
    print("OK 上传 HTTP 401 → NamiDeployError")


def test_empty_html_raises():
    try:
        nd.deploy_html("   ")
        assert False, "空 HTML 应抛"
    except nd.NamiDeployError:
        pass
    print("OK 空 HTML → NamiDeployError")


def main():
    test_html_zip_root_index()
    test_signed_headers_windows_safe()
    test_deploy_success_short()
    test_short_fail_falls_back_baseurl()
    test_no_vmid_returns_baseurl()
    test_upload_fail_raises()
    test_upload_http_error_raises()
    test_empty_html_raises()
    print("OK test_nami_deploy")


if __name__ == "__main__":
    main()
