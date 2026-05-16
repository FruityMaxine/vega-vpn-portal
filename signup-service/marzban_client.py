"""
Marzban API 客户端包装：
- 维护 admin JWT token（自动刷新，过期前 60 秒重拿）
- 暴露 create_user / get_user / get_sub_info 三类方法
"""
from __future__ import annotations
import os
import time
import secrets
import asyncio
import httpx
from typing import Optional


class MarzbanClient:
    def __init__(self,
                 base_url: Optional[str] = None,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 timeout: float = 10.0):
        self.base_url = (base_url or os.environ.get("MARZBAN_API_BASE", "http://127.0.0.1:8000")).rstrip("/")
        self.username = username or os.environ.get("MARZBAN_ADMIN_USER", "admin")
        self.password = password or os.environ.get("MARZBAN_ADMIN_PASS", "")
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        self._lock = asyncio.Lock()
        self._timeout = timeout

    async def _get_token(self) -> str:
        async with self._lock:
            now = time.time()
            if self._token and now < self._token_exp - 60:
                return self._token
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(
                    f"{self.base_url}/api/admin/token",
                    data={"username": self.username, "password": self.password},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                r.raise_for_status()
                data = r.json()
                self._token = data["access_token"]
                # JWT 默认 24h（1440 分钟），保守按 23h 缓存
                self._token_exp = now + 23 * 3600
                return self._token

    async def _auth_headers(self) -> dict:
        t = await self._get_token()
        return {"Authorization": f"Bearer {t}"}

    async def create_user(self,
                          username: str,
                          data_limit_gb: int,
                          expire_days: int,
                          reset_strategy: str,
                          proxies: list[str],
                          inbounds_map: dict[str, list[str]],
                          note: str = "") -> dict:
        """
        v0.2 签名：直接接收 data_limit_gb / expire_days / reset_strategy。
        proxies: 类似 ["vless", "shadowsocks"]
        inbounds_map: {"vless": ["VLESS Reality"], ...}
        """
        GB = 1024 ** 3
        data_limit_bytes = int(data_limit_gb) * GB
        expire_ts = int(time.time()) + int(expire_days) * 86400
        body: dict = {
            "username": username,
            "proxies": {p: {} for p in proxies},
            "inbounds": {p: inbounds_map.get(p, []) for p in proxies},
            "data_limit": data_limit_bytes,
            "data_limit_reset_strategy": reset_strategy,
            "expire": expire_ts,
            "note": note,
            "status": "active",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(
                f"{self.base_url}/api/user",
                json=body,
                headers={**(await self._auth_headers()), "Content-Type": "application/json"},
            )
            if r.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"Marzban create_user failed: {r.status_code} {r.text}",
                    request=r.request, response=r,
                )
            return r.json()

    async def get_user(self, username: str) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{self.base_url}/api/user/{username}",
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
            return r.json()

    async def list_users(self) -> dict:
        """返回 {'users': [...], 'total': N}，含 used_traffic / lifetime_used_traffic"""
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{self.base_url}/api/users",
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
            return r.json()

    async def get_inbounds(self) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{self.base_url}/api/inbounds",
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
            return r.json()

    async def set_status(self, username: str, status: str) -> dict:
        """status: active / disabled"""
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.put(
                f"{self.base_url}/api/user/{username}",
                json={"status": status},
                headers={**(await self._auth_headers()), "Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.json()

    async def delete_user(self, username: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.delete(
                f"{self.base_url}/api/user/{username}",
                headers=await self._auth_headers(),
            )
            if r.status_code not in (200, 204, 404):
                r.raise_for_status()

    async def get_sub_info(self, sub_token: str) -> dict:
        """公开端点，不需要 admin token"""
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{self.base_url}/sub/{sub_token}/info",
                headers={"User-Agent": "vega-signup/1.0"},
            )
            if r.status_code == 404:
                raise ValueError("subscription token not found")
            r.raise_for_status()
            return r.json()


def gen_shortid(n: int = 6) -> str:
    return secrets.token_hex(n // 2 + 1)[:n]
