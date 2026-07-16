"""Session 管理：基于 Homebox 登录 token 的签名 cookie。

登录流程：前端把 Homebox 账号密码 POST 到 /api/login，后端用这些凭据调用
Homebox 的 /api/v1/users/login 拿到 JWT，再把 {token, email} 序列化后做
HMAC-SHA256 签名，写入 httpOnly + SameSite=Lax(+Secure) 的 cookie。

之后所有需要访问 Homebox 的接口都从 cookie 解出 token 使用，实现「共用 Homebox
登录信息」——谁登录就用谁的 Homebox 账号与权限。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets

# 服务端密钥，用于给 cookie 签名（防篡改）。建议通过 APP_SECRET 环境变量固定，
# 否则每次进程重启会重新生成，导致已签发的 cookie 失效（用户需重新登录）。
SECRET = os.getenv("APP_SECRET") or secrets.token_hex(32)
COOKIE_NAME = "b2h_session"
MAX_AGE = 60 * 60 * 24 * 7  # 7 天


def _sign(payload_b64: str) -> str:
    return hmac.new(SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()


def encode_session(token: str, email: str = "") -> str:
    """把 Homebox token + email 序列化并签名，返回 cookie 值。"""
    data = json.dumps({"t": token, "e": email}).encode("utf-8")
    payload = base64.urlsafe_b64encode(data).decode()
    return f"{payload}.{_sign(payload)}"


def decode_session(value: str | None) -> dict | None:
    """校验签名并返回 {token, email}，无效返回 None。"""
    if not value or "." not in value:
        return None
    payload, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
        if not data.get("t"):
            return None
        return data
    except Exception:  # noqa: BLE001
        return None
