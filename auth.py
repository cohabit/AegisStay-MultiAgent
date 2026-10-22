"""Service credentials and expiring, room/stay/session-bound guest passes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Callable

from fastapi import Header, HTTPException

STAFF_ROLES = {"admin", "frontdesk", "housekeeping", "engineering", "manager"}


@dataclass(frozen=True)
class Principal:
    role: str
    subject: str
    session_id: str | None = None
    room_no: str | None = None
    stay_id: str | None = None
    grant_id: str | None = None
    expires_at: int | None = None

    @property
    def staff(self) -> bool:
        return self.role in STAFF_ROLES or self.role == "local"

    def public(self) -> dict:
        return asdict(self)


def enabled() -> bool:
    return os.getenv("AEGIS_AUTH_ENABLED", "").lower() in {"1", "true", "yes"}


def validate_configuration() -> None:
    if os.getenv("AEGIS_DEPLOYED") == "1":
        if not enabled() or not os.getenv("AEGIS_DB_PATH"):
            raise RuntimeError("部署模式必须启用鉴权和持久化")
    if not enabled():
        return
    for name in ("AEGIS_STAFF_TOKEN", "AEGIS_SIGNING_KEY"):
        value = os.getenv(name, "")
        if len(value) < 32 or value.startswith("replace-"):
            raise RuntimeError(f"{name} 必须配置为至少 32 字符的随机密钥")
    if os.getenv("AEGIS_STAFF_TOKEN") == os.getenv("AEGIS_SIGNING_KEY"):
        raise RuntimeError("员工凭据和住客签名密钥必须不同")
    try:
        credentials = json.loads(os.getenv("AEGIS_STAFF_KEYS_JSON", "{}"))
        if not isinstance(credentials, dict):
            raise ValueError()
        for token, person in credentials.items():
            if (len(token) < 32 or not token.isascii() or token in {os.getenv("AEGIS_STAFF_TOKEN"), os.getenv("AEGIS_SIGNING_KEY")}
                    or not isinstance(person, dict) or person.get("role") not in STAFF_ROLES
                    or not isinstance(person.get("subject"), str) or not person["subject"].strip()):
                raise ValueError()
    except (ValueError, TypeError):
        raise RuntimeError("个人岗位凭据配置无效，请核对随机密钥、岗位和员工标识") from None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def issue_guest_pass(principal: Principal) -> str:
    if principal.role != "guest":
        raise ValueError("只能签发住客凭证")
    key = os.getenv("AEGIS_SIGNING_KEY", "")
    if len(key) < 32:
        raise RuntimeError("未配置住客签名密钥")
    body = _b64(json.dumps(principal.public(), separators=(",", ":"), ensure_ascii=True).encode())
    signature = _b64(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())
    return body + "." + signature


def authenticate(authorization: str | None = Header(default=None)) -> Principal:
    if not enabled():
        return Principal("local", "local-development")
    validate_configuration()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "需要访问凭证")
    token = authorization.split(" ", 1)[1].strip()
    if not token.isascii() or not token or len(token) > 8192:
        raise HTTPException(401, "凭证格式无效")
    if hmac.compare_digest(token, os.getenv("AEGIS_STAFF_TOKEN", "")):
        return Principal("admin", "hotel-admin")
    # A mapping of individual staff tokens is supplied only as a deployment secret.
    try:
        credentials = json.loads(os.getenv("AEGIS_STAFF_KEYS_JSON", "{}"))
        for staff_token, identity in credentials.items():
            if len(staff_token) >= 32 and hmac.compare_digest(token, staff_token):
                if identity.get("role") not in STAFF_ROLES:
                    break
                return Principal(identity["role"], identity["subject"])
        body, signature = token.split(".")
        expected = _b64(hmac.new(os.environ["AEGIS_SIGNING_KEY"].encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        identity = Principal(**data)
        if (identity.role != "guest" or not all((identity.session_id, identity.room_no, identity.stay_id, identity.grant_id))
                or not identity.expires_at or identity.expires_at <= time.time()):
            raise ValueError("expired or invalid")
        return identity
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "凭证无效或已过期") from None


def require_role(required: str) -> Callable:
    def dependency(authorization: str | None = Header(default=None)) -> Principal:
        principal = authenticate(authorization)
        if required == "staff" and not principal.staff:
            raise HTTPException(403, "需要员工权限")
        return principal
    return dependency


def require_staff(principal: Principal, roles: set[str] | None = None) -> None:
    if not principal.staff or (roles and principal.role not in roles | {"local", "admin"}):
        raise HTTPException(403, "当前岗位无权执行该操作")


def require_session(principal: Principal, session_id: str, room_no: str | None = None) -> None:
    if principal.staff:
        require_staff(principal, {"frontdesk", "manager"})
        return
    if principal.session_id != session_id or room_no and principal.room_no != room_no:
        raise HTTPException(403, "凭证不属于该会话或房间")
