"""复用 qihoo-sso-cli 的客户端配置，不读取任何 CLI 用户登录凭据。"""
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet

from app.core.config import settings

# qihoo-sso-cli 1.6.0 lib/config.js 的公共客户端默认值。
DEFAULT_SSO_URL = "https://sts.login.ops.qihoo.net:4436"
DEFAULT_CLIENT_ID = "a14ca4e6e00a488991b15501901fafbd"
_BACKEND = Path(__file__).resolve().parents[2]


def client_config() -> dict:
    paths = ([Path(settings.GEELIB_SSO_CONFIG_FILE).expanduser()] if settings.GEELIB_SSO_CONFIG_FILE else
             [Path(Path.cwd().anchor) / "etc/qihoo-sso-cli/config.json",
              Path.home() / ".qihoo-sso-cli/config.json"])
    config = {}
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError()
            # 仅允许客户端元数据，绝不读取 auth/config.json、token.json 或 SSO_ACCESS_TOKEN。
            config.update({k: value[k] for k in ("sso_url", "client_id", "client_secret") if k in value})
        except FileNotFoundError:
            if settings.GEELIB_SSO_CONFIG_FILE:
                raise ValueError("指定的 SSO 客户端配置文件不存在") from None
        except (OSError, ValueError):
            raise ValueError("无法读取 SSO 客户端配置文件，请检查文件权限和 JSON 格式") from None
    result = {
        "sso_url": settings.GEELIB_SSO_URL or os.environ.get("SSO_URL") or config.get("sso_url") or DEFAULT_SSO_URL,
        "client_id": settings.GEELIB_OAUTH_CLIENT_ID or os.environ.get("SSO_CLIENT_ID") or config.get("client_id") or DEFAULT_CLIENT_ID,
        "client_secret": settings.GEELIB_OAUTH_CLIENT_SECRET or os.environ.get("SSO_CLIENT_SECRET") or config.get("client_secret") or "",
    }
    if not all(isinstance(v, str) for v in result.values()):
        raise ValueError("SSO 客户端配置格式错误")
    url = urlparse(result["sso_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("SSO 服务地址必须是有效的 HTTPS 地址")
    result["sso_url"] = result["sso_url"].rstrip("/")
    return result


def credential_cipher() -> Fernet:
    if settings.GEELIB_TOKEN_ENCRYPTION_KEY:
        try:
            return Fernet(settings.GEELIB_TOKEN_ENCRYPTION_KEY.encode())
        except (ValueError, TypeError):
            raise ValueError("配置的极库云加密密钥无效，请使用有效的 Fernet 密钥") from None
    path = (Path(settings.GEELIB_TOKEN_KEY_FILE).expanduser() if settings.GEELIB_TOKEN_KEY_FILE else
            _BACKEND / ".secrets/geelib.key")
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # 写完整后以硬链接原子发布，多个 worker 同时首次启动只保留一个密钥。
            # 不使用 replace，防止并发覆盖已有密钥；不把密钥写入数据库或日志。
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return Fernet(path.read_bytes().strip())
    except OSError:
        raise ValueError("无法保存极库云授权密钥，请为后端 .secrets 目录提供持久化写权限，或配置 GEELIB_TOKEN_KEY_FILE") from None
    except (ValueError, TypeError):
        raise ValueError("极库云授权密钥文件损坏，请恢复原密钥备份") from None
