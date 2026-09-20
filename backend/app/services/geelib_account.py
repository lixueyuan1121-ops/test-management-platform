"""按平台 user_id 隔离的极库云 SSO 授权。

接口契约来自 qihoo-sso-cli：OAuth 授权码/刷新 + /oauth/cli/authorize。
使用 OOB 授权码和 PKCE，用户在自己的浏览器登录；后端不读取服务器 CLI 凭据。
"""
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.geelib_account import GeelibAccount
from app.services.geelib import GeelibError
from app.services.geelib_sso_config import client_config, credential_cipher

_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


class GeelibAccountError(GeelibError):
    """个人授权不可用；HTTP 层使用 409，不触发平台 JWT 的 401 刷新。"""


def _cipher() -> Fernet:
    try:
        return credential_cipher()
    except ValueError as exc:
        raise GeelibAccountError(str(exc)) from None


def configuration_error() -> str | None:
    try:
        client_config()
        _cipher()
    except (ValueError, GeelibAccountError) as exc:
        return str(exc)
    return None


def configured() -> bool:
    return configuration_error() is None


def _require_config():
    if not settings.GEELIB_ENABLED:
        raise GeelibAccountError("极库云上报通道未启用")
    error = configuration_error()
    if error:
        raise GeelibAccountError(error)


def _encrypt(data: dict) -> str:
    return _cipher().encrypt(json.dumps(data).encode()).decode()


def _decrypt(value: str) -> dict:
    try:
        return json.loads(_cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError, TypeError):
        raise GeelibAccountError("极库云授权无法读取，请重新绑定个人账号") from None


def account_status(db: Session, user_id: int) -> dict:
    row = db.get(GeelibAccount, user_id)
    error = configuration_error() if settings.GEELIB_ENABLED else "极库云上报通道未启用"
    return {"enabled": settings.GEELIB_ENABLED, "configured": error is None, "configuration_error": error,
            "bound": bool(row and row.credentials),
            "account_name": row.account_name if row and row.credentials else None}


def start_authorization(db: Session, user_id: int) -> dict:
    _require_config()
    client = client_config()
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    row = db.get(GeelibAccount, user_id)
    if row is None:
        row = GeelibAccount(user_id=user_id)
        db.add(row)
    row.pending_auth = _encrypt({"state": state, "verifier": verifier, "expires_at": time.time() + 300})
    db.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"client_id": client["client_id"], "redirect_uri": _REDIRECT_URI,
              "scope": settings.GEELIB_OAUTH_SCOPE, "response_type": "code", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    return {"authorization_url": f"{client['sso_url']}/oauth/authorize?{urlencode(params)}",
            "state": state, "expires_in": 300}


def _post(path: str, **kwargs) -> dict:
    try:
        resp = requests.post(f"{client_config()['sso_url']}{path}",
                             timeout=30, allow_redirects=False, **kwargs)
        data = resp.json()
    except (requests.RequestException, ValueError):
        raise GeelibAccountError("极库云 SSO 暂不可用，请稍后重试") from None
    if resp.status_code != 200 or not isinstance(data, dict):
        raise GeelibAccountError("极库云 SSO 授权失败，请重新绑定个人账号")
    # 不回显服务端原文，避免上游响应包含凭据。
    return data


def _credentials(data: dict, previous: dict | None = None) -> dict:
    if not isinstance(data.get("access_token"), str) or not data["access_token"]:
        raise GeelibAccountError("极库云 SSO 未返回有效授权，请重新绑定")
    try:
        expires_in = max(0, float(data.get("expires_in", 300)))
    except (TypeError, ValueError):
        raise GeelibAccountError("极库云 SSO 返回无效的授权有效期") from None
    return {"access_token": data["access_token"],
            "refresh_token": data.get("refresh_token") or (previous or {}).get("refresh_token"),
            "expires_at": time.time() + expires_in,
            "agent_instance_id": data.get("agent_instance_id") or (previous or {}).get("agent_instance_id"),
            "agent_name": data.get("agent_name") or (previous or {}).get("agent_name")}


def finish_authorization(db: Session, user_id: int, state: str, code: str) -> dict:
    _require_config()
    client = client_config()
    row = db.query(GeelibAccount).filter_by(user_id=user_id).with_for_update().first()
    if not row or not row.pending_auth:
        raise GeelibAccountError("请先发起个人账号授权")
    pending = _decrypt(row.pending_auth)
    if pending.get("claimed") or pending["expires_at"] <= time.time() or not secrets.compare_digest(pending["state"], state):
        raise GeelibAccountError("授权会话无效或已过期，请重新发起绑定")
    # 一次性消费，错误重试也必须重新发起，避免授权码并发交换。
    pending["claimed"] = True
    claimed = _encrypt(pending)
    row.pending_auth = claimed
    db.commit()
    data = _post("/oauth/token", data={"grant_type": "authorization_code", "code": code.strip(),
                 "client_id": client["client_id"],
                 "client_secret": client["client_secret"],
                 "redirect_uri": _REDIRECT_URI, "code_verifier": pending["verifier"]})
    # 授权交换期间如用户解绑/重新发起绑定，不覆盖新状态。
    db.expire_all()
    row = db.query(GeelibAccount).filter_by(user_id=user_id).with_for_update().first()
    if not row or row.pending_auth != claimed:
        raise GeelibAccountError("授权会话已变更，请重新绑定")
    row.pending_auth = None
    row.credentials = _encrypt(_credentials(data))
    row.account_name = str(data.get("username") or "已授权个人账号")[:128]
    db.commit()
    return account_status(db, user_id)


def disconnect(db: Session, user_id: int) -> None:
    row = db.query(GeelibAccount).filter_by(user_id=user_id).with_for_update().first()
    if row:
        db.delete(row)
        db.commit()


def get_user_app_token(db: Session, user_id: int) -> str:
    _require_config()
    client = client_config()
    row = db.query(GeelibAccount).filter_by(user_id=user_id).with_for_update().first()
    if not row or not row.credentials:
        raise GeelibAccountError("请先绑定我的极库云账号，再报送 Bug")
    creds = _decrypt(row.credentials)
    if creds["expires_at"] <= time.time() + 30:
        if not creds.get("refresh_token"):
            raise GeelibAccountError("个人极库云授权已过期，请重新绑定")
        data = _post("/oauth/token", data={"grant_type": "refresh_token",
                     "refresh_token": creds["refresh_token"], "client_id": client["client_id"],
                     "client_secret": client["client_secret"]})
        creds = _credentials(data, creds)
        row.credentials = _encrypt(creds)
    # 刷新后的凭据先持久化；业务授权可能需要用户在 SSO 的 IM 卡片里确认。
    db.commit()
    body = {"app_name": settings.GEELIB_SSO_APP, "tool_name": settings.GEELIB_SSO_TOOL,
            "reason": "测试管理平台：使用我的账号报送和更新缺陷"}
    for key in ("agent_instance_id", "agent_name"):
        if creds.get(key):
            body[key] = creds[key]
    data = _post("/oauth/cli/authorize", json=body,
                 headers={"Authorization": f"Bearer {creds['access_token']}", "X-Qihoo-SSO-CLI-Mode": "raw-json"})
    token = data.get("app_token")
    if data.get("errcode") not in (0, "0") or not isinstance(token, str) or not token.strip():
        raise GeelibAccountError("个人极库云业务授权未完成，请在 SSO 确认授权后重试，或重新绑定账号")
    return token
