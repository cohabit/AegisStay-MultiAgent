"""Minimal bearer-token boundary for the single-hotel deployment.

The application deliberately keeps authentication outside the business engine.
Set ``AEGIS_AUTH_ENABLED=1`` together with ``AEGIS_GUEST_TOKEN`` and
``AEGIS_STAFF_TOKEN`` in a secret manager.  The default is disabled for local
offline tests; a production deployment should enable it behind HTTPS.
"""
from __future__ import annotations

import os
from typing import Callable

from fastapi import Header, HTTPException


def _enabled() -> bool:
    return os.getenv("AEGIS_AUTH_ENABLED", "").lower() in {"1", "true", "yes"}


def require_role(required: str) -> Callable[..., str]:
    def dependency(authorization: str | None = Header(default=None)) -> str:
        if not _enabled():
            return "local"
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="需要 Bearer 访问令牌")
        token = authorization.split(" ", 1)[1].strip()
        staff_token = os.getenv("AEGIS_STAFF_TOKEN", "")
        guest_token = os.getenv("AEGIS_GUEST_TOKEN", "")
        if staff_token and token == staff_token:
            return "staff"
        if required == "guest" and guest_token and token == guest_token:
            return "guest"
        raise HTTPException(status_code=403, detail="当前令牌没有访问该资源的权限")

    return dependency
