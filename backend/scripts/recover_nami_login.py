"""Import a browser login without exposing credentials or executing copied cURL.

Run from backend/: .venv/bin/python -m scripts.recover_nami_login
In Chrome Network, copy a request to https://www.n.cn as cURL. Paste it into
the hidden prompt, then enter END_NAMI_REQUEST on its own line.
"""
import json
import io
import os
from pathlib import Path
import shlex
import sys
import tempfile
import termios
from urllib.parse import urlsplit

from app.services import nami_deploy as nd
from scripts.refresh_nami_cookie import _TEST_HTML

_END = "END_NAMI_REQUEST"
_MAX_INPUT = 65536


def cookie_from_curl(raw: str) -> str:
    """Parse text only: never run a shell or follow the copied request URL."""
    try:
        if len(raw) > _MAX_INPUT:
            raise ValueError
        raw = raw.replace("\x1b[200~", "").replace("\x1b[201~", "")
        args = shlex.split(raw.replace("\\\r\n", "").replace("\\\n", ""))
        if len(args) < 2 or args[0] != "curl":
            raise ValueError
        url = urlsplit(args[1])
        if url.scheme != "https" or url.netloc != "www.n.cn":
            raise ValueError
        cookies = []
        for i, arg in enumerate(args[2:], 2):
            if arg in ("-b", "--cookie"):
                cookies.append(args[i + 1])
            elif arg.startswith("--cookie="):
                cookies.append(arg.split("=", 1)[1])
            elif arg in ("-H", "--header"):
                header = args[i + 1]
                if header.lower().startswith("cookie:"):
                    cookies.append(header.split(":", 1)[1].strip())
        if len(cookies) != 1:
            raise ValueError
        cookie = cookies[0]
        if any(ord(c) < 32 or ord(c) == 127 for c in cookie):
            raise ValueError
        values = dict(item.strip().split("=", 1) for item in cookie.split(";") if "=" in item)
        if not values.get("Q") or not values.get("T"):
            raise ValueError
        return cookie
    except (ValueError, IndexError):
        # Parser exceptions can quote the input: replace them with a fixed message.
        raise ValueError("需要复制发往 https://www.n.cn 的完整 cURL，且包含有效的 Q/T Cookie。") from None


def read_hidden_request() -> str:
    """Fail closed unless a real terminal can disable echo for the entire paste."""
    with io.TextIOWrapper(io.FileIO("/dev/tty", "r+"), encoding="utf-8") as tty:
        fd = tty.fileno()
        previous = termios.tcgetattr(fd)
        hidden = previous.copy()
        hidden[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON)
        hidden[6] = previous[6].copy()
        hidden[6][termios.VMIN] = 1
        hidden[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSAFLUSH, hidden)
        try:
            tty.write(f"Paste Nami cURL (hidden), then Enter + {_END} + Enter:\n")
            tty.flush()
            lines = []
            size = 0
            oversized = False
            while True:
                line = tty.readline()
                if not line:
                    raise ValueError("输入已取消。")
                if line.replace("\x1b[200~", "").replace("\x1b[201~", "").strip() == _END:
                    break
                size += len(line)
                if size > _MAX_INPUT:
                    oversized = True
                if not oversized:
                    lines.append(line)
            if oversized:
                raise ValueError("输入过长，未更新配置。")
            return "".join(lines)
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, previous)
            tty.write("\n")
            tty.flush()


def verify_cookie(cookie: str) -> bool:
    vm_id = nd._read_vm_id()
    if not vm_id:
        print("短链配置缺少有效 vm_id，验证未通过。")
        return False
    try:
        base_url = nd._upload(_TEST_HTML, cookie)
        short = nd._get_short(vm_id, base_url, cookie)
    except nd.NamiDeployError:
        # Provider errors may include request/response data; never print them here.
        print("Nami 上传或短链验证失败，未更新配置。")
        return False
    print(f"短链 OK：{short}")
    return True


def install_verified_cookie(cookie: str, path: Path, verify=None) -> bool:
    """Verify before any write; keep private backup and atomically replace config."""
    if not (verify or verify_cookie)(cookie):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        fd, backup = tempfile.mkstemp(prefix=path.name + ".bak-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(path.read_bytes())
        print(f"旧配置已备份：{backup}")
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"cookie": cookie}, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main() -> int:
    try:
        cookie = cookie_from_curl(read_hidden_request())
        print("请求格式检查通过，开始验证短链。")
        if not install_verified_cookie(cookie, nd._cookie_path()):
            print("验证失败，原配置未修改。")
            return 1
    except ValueError as exc:
        print(str(exc))
        return 1
    except (OSError, KeyboardInterrupt):
        print("恢复未完成：输入、终端或文件操作失败，未确认更新成功。凭据内容不会输出。")
        return 1
    print("RECOVERY_OK：短链验证通过，新 Cookie 已原子写入（权限 600），无需重启服务。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
