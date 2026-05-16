"""
Vega VPN Signup Service
FastAPI 中间层 · 朋友账户系统 + 邀请码自助激活 + 流量自查 + 管理员后台

v0.4.0.0 新增：
- accounts 表（用户名 + bcrypt 密码 + JWT session cookie 多设备登录）
- 一个账户绑多个 Marzban 订阅（多次激活不同邀请码）
- 管理员后台账户视图 + 禁用/启用/重置密码/删订阅
- 旧 /api/signup 和 /api/me/{token} 端点保留兼容
"""
from __future__ import annotations
import os
import re
import json
import base64
import sqlite3
import secrets
import time
import io
import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal
from pathlib import Path

import httpx
import qrcode
import jwt
from passlib.context import CryptContext
from fastapi import FastAPI, HTTPException, Depends, Header, Request, Response, Cookie
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from marzban_client import MarzbanClient, gen_shortid

# ============================================================
# 配置 & 文件路径
# ============================================================
DATA_DIR = Path(os.environ.get("SIGNUP_DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
INVITES_FILE = DATA_DIR / "invites.json"
DB_FILE = DATA_DIR / "signups.sqlite"
ADMIN_TOKEN_FILE = DATA_DIR / "admin_token.txt"
JWT_SECRET_FILE = DATA_DIR / "jwt_secret.txt"

PORTAL_BASE_URL = os.environ.get("PORTAL_BASE_URL", "https://vpn.example.com")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("signup")

ALLOWED_RESET = ("day", "week", "month", "year", "no_reset")
DEFAULT_PROXIES = ["vless", "shadowsocks", "trojan", "vmess"]

SESSION_COOKIE = "va_session"
SESSION_MAX_AGE = 30 * 86400  # 30 天

RESERVED_USERNAMES = {"admin", "root", "system", "api", "support", "vega", "marzban", "test"}

pwd_ctx = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12, deprecated="auto")

# ============================================================
# Admin token
# ============================================================
def load_admin_token() -> str:
    if not ADMIN_TOKEN_FILE.exists():
        tok = secrets.token_hex(24)
        ADMIN_TOKEN_FILE.write_text(tok + "\n")
        log.warning("admin_token.txt 不存在已自动生成: %s", tok)
    return ADMIN_TOKEN_FILE.read_text().strip()


ADMIN_TOKEN = load_admin_token()


def load_jwt_secret() -> str:
    if not JWT_SECRET_FILE.exists():
        s = secrets.token_hex(32)
        JWT_SECRET_FILE.write_text(s + "\n")
        log.warning("jwt_secret.txt 不存在已自动生成")
    return JWT_SECRET_FILE.read_text().strip()


JWT_SECRET = load_jwt_secret()


def require_admin(authorization: str = Header(default="")) -> None:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    tok = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(tok, ADMIN_TOKEN):
        raise HTTPException(403, "invalid admin token")


# ============================================================
# 全局 prefs
# ============================================================
DEFAULT_CONFIG = {
    "allowed_reset_strategies": list(ALLOWED_RESET),
    "default_proxies": DEFAULT_PROXIES,
}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False))
        return dict(DEFAULT_CONFIG)
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        cfg["allowed_reset_strategies"] = list(ALLOWED_RESET)
        return cfg
    except Exception as e:
        log.error("config.json 解析失败 %s, 回退默认", e)
        return dict(DEFAULT_CONFIG)


def load_invites() -> list[dict]:
    if not INVITES_FILE.exists():
        INVITES_FILE.write_text(json.dumps({"invites": []}, indent=2))
        return []
    try:
        return json.loads(INVITES_FILE.read_text()).get("invites", [])
    except Exception:
        return []


def save_invites(invites: list[dict]) -> None:
    INVITES_FILE.write_text(json.dumps({"invites": invites}, indent=2, ensure_ascii=False))


# ============================================================
# SQLite
# ============================================================
def db_init() -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invite_code TEXT NOT NULL,
                friend_name TEXT NOT NULL,
                marzban_username TEXT NOT NULL UNIQUE,
                subscription_url TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                client_ip TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signups_invite ON signups(invite_code)")
        # v0.4 加 account_id 列（migration safe）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(signups)").fetchall()]
        if "account_id" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN account_id INTEGER")
            log.info("migration: signups + account_id")
        if "subscription_token" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN subscription_token TEXT")
        if "deleted_at" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN deleted_at TEXT")
            log.info("migration: signups + subscription_token")
            # 回填 token（从 subscription_url 解出）
            rows = conn.execute("SELECT id, subscription_url FROM signups WHERE subscription_token IS NULL").fetchall()
            for rid, surl in rows:
                tok = surl.rsplit("/sub/", 1)[-1].rstrip("/") if surl and "/sub/" in surl else ""
                conn.execute("UPDATE signups SET subscription_token=? WHERE id=?", (tok, rid))

        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_login_at TEXT,
                disabled INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signups_account ON signups(account_id)")
        conn.commit()


db_init()


@contextmanager
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ============================================================
# Marzban client
# ============================================================
mz = MarzbanClient()

# ============================================================
# FastAPI
# ============================================================
app = FastAPI(title="Vega VPN Signup Service", version="0.6.0.0")


RESET_SPEED = {"day": 0, "week": 1, "month": 2, "year": 3, "no_reset": 4}


def allowed_resets_for(min_strategy: str) -> list[str]:
    if min_strategy not in RESET_SPEED:
        return ["no_reset"]
    base = RESET_SPEED[min_strategy]
    return [s for s in ("day", "week", "month", "year", "no_reset") if RESET_SPEED[s] >= base]


def invite_caps(inv: dict) -> tuple[int, int, str]:
    max_data = int(inv.get("max_data_limit_gb", inv.get("data_limit_gb", 0)))
    max_exp = int(inv.get("max_expire_days", inv.get("expire_days", 0)))
    min_reset = inv.get("min_reset_strategy", inv.get("reset_strategy", "no_reset"))
    if min_reset not in RESET_SPEED:
        min_reset = "no_reset"
    return max_data, max_exp, min_reset


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


# ============================================================
# Models & regex
# ============================================================
FRIEND_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,16}$")
ACCOUNT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{2,24}$")
INVITE_RE = re.compile(r"^[A-Z0-9\-]{6,32}$")


class SignupRequest(BaseModel):
    """旧版 /api/signup —— 保留兼容（不创建账户）"""
    invite_code: str = Field(min_length=4, max_length=32)
    friend_name: str = Field(min_length=2, max_length=16)
    data_limit_gb: int = Field(ge=1, le=10000)
    expire_days: int = Field(ge=1, le=3650)
    reset_strategy: Literal["day", "week", "month", "year", "no_reset"]

    @field_validator("invite_code")
    @classmethod
    def _ic(cls, v: str) -> str:
        v = v.strip().upper()
        if not INVITE_RE.match(v):
            raise ValueError("invite_code 格式不合法")
        return v

    @field_validator("friend_name")
    @classmethod
    def _fn(cls, v: str) -> str:
        if not FRIEND_NAME_RE.match(v):
            raise ValueError("friend_name 只能字母开头 + 字母/数字/下划线，2-16 位")
        return v


class AccountSignupRequest(BaseModel):
    invite_code: str = Field(min_length=4, max_length=32)
    username: str = Field(min_length=2, max_length=24)
    password: str = Field(min_length=6, max_length=128)
    data_limit_gb: int = Field(ge=1, le=10000)
    expire_days: int = Field(ge=1, le=3650)
    reset_strategy: Literal["day", "week", "month", "year", "no_reset"]

    @field_validator("invite_code")
    @classmethod
    def _ic(cls, v: str) -> str:
        v = v.strip().upper()
        if not INVITE_RE.match(v):
            raise ValueError("invite_code 格式不合法")
        return v

    @field_validator("username")
    @classmethod
    def _un(cls, v: str) -> str:
        v = v.strip()
        if not ACCOUNT_USERNAME_RE.match(v):
            raise ValueError("username 必须 2-24 位字母/数字/下划线")
        if v.lower() in RESERVED_USERNAMES:
            raise ValueError("该用户名为保留字，不可使用")
        return v


class AccountLoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=24)
    password: str = Field(min_length=1, max_length=128)


class RedeemRequest(BaseModel):
    invite_code: str = Field(min_length=4, max_length=32)
    data_limit_gb: int = Field(ge=1, le=10000)
    expire_days: int = Field(ge=1, le=3650)
    reset_strategy: Literal["day", "week", "month", "year", "no_reset"]

    @field_validator("invite_code")
    @classmethod
    def _ic(cls, v: str) -> str:
        v = v.strip().upper()
        if not INVITE_RE.match(v):
            raise ValueError("invite_code 格式不合法")
        return v


class InviteCreate(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    max_data_limit_gb: int = Field(ge=1, le=10000)
    max_expire_days: int = Field(ge=1, le=3650)
    min_reset_strategy: Literal["day", "week", "month", "year", "no_reset"]
    note: str = Field(default="", max_length=120)


# ============================================================
# 工具
# ============================================================
GB = 1024 ** 3
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_qr_data_url(text: str) -> str:
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def humanize_bytes(b) -> str:
    if b is None:
        return "无限"
    b = float(b)
    if b <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"


def next_reset_seconds(strategy: str, now: Optional[datetime] = None) -> Optional[int]:
    now = now or datetime.now(timezone.utc)
    if strategy == "day":
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return int((nxt - now).total_seconds())
    if strategy == "week":
        days_to_mon = (7 - now.weekday()) % 7 or 7
        nxt = (now + timedelta(days=days_to_mon)).replace(hour=0, minute=0, second=0, microsecond=0)
        return int((nxt - now).total_seconds())
    if strategy == "month":
        if now.month == 12:
            nxt = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            nxt = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return int((nxt - now).total_seconds())
    if strategy == "year":
        nxt = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return int((nxt - now).total_seconds())
    return None


def gen_invite_code() -> str:
    def chunk(n): return "".join(secrets.choice(ALPHABET) for _ in range(n))
    return f"{chunk(3)}-{chunk(4)}-{chunk(4)}"


# ============================================================
# Session / JWT
# ============================================================
def make_session_jwt(account_id: int, username: str) -> str:
    payload = {
        "sub": str(account_id),
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + SESSION_MAX_AGE,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def set_session_cookie(resp: Response, account_id: int, username: str) -> None:
    token = make_session_jwt(account_id, username)
    resp.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_MAX_AGE,
        httponly=True, secure=True, samesite="lax", path="/",
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(SESSION_COOKIE, path="/")


def decode_session(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def current_account(va_session: Optional[str] = Cookie(default=None)) -> dict:
    """守门: 返回 account row 或 401"""
    data = decode_session(va_session)
    if not data:
        raise HTTPException(401, "未登录或会话已过期")
    aid = int(data.get("sub") or 0)
    with db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
        if not row:
            raise HTTPException(401, "账户不存在")
        if row["disabled"]:
            raise HTTPException(403, "账户已被禁用")
        return dict(row)


# ============================================================
# 失败登录限速 in-memory
# ============================================================
_login_fail: dict[str, list[float]] = {}


def check_rate_limit(ip: str) -> None:
    now = time.time()
    arr = _login_fail.get(ip, [])
    arr = [t for t in arr if now - t < 60]
    _login_fail[ip] = arr
    if len(arr) >= 5:
        raise HTTPException(429, "登录失败次数过多，请稍后再试")


def record_login_fail(ip: str) -> None:
    _login_fail.setdefault(ip, []).append(time.time())


# ============================================================
# Marzban user 创建辅助
# ============================================================
async def _create_marzban_user_for_invite(
    inv: dict, payload_data_gb: int, payload_expire_days: int, payload_reset: str,
    label_username: str, note: str,
) -> tuple[str, str, str]:
    """返回 (marzban_username, subscription_url, subscription_token)"""
    cfg = load_config()
    max_data, max_exp, min_reset = invite_caps(inv)
    allowed = allowed_resets_for(min_reset)

    if payload_data_gb < 1 or payload_data_gb > max_data:
        raise HTTPException(400, f"流量超出邀请码上限：必须在 1 ~ {max_data} GB 之间")
    if payload_expire_days < 1 or payload_expire_days > max_exp:
        raise HTTPException(400, f"有效期超出邀请码上限：必须在 1 ~ {max_exp} 天之间")
    if payload_reset not in allowed:
        raise HTTPException(400, f"重置频率不在允许列表内：仅可选 {allowed}")

    safe = re.sub(r"[^A-Za-z0-9_]", "", label_username)[:12] or "user"
    username = ""
    for _ in range(8):
        cand = f"{safe}_{gen_shortid(6)}"
        with db() as conn:
            row = conn.execute("SELECT 1 FROM signups WHERE marzban_username=?", (cand,)).fetchone()
            if not row:
                username = cand
                break
    if not username:
        raise HTTPException(500, "无法生成唯一用户名，请重试")

    try:
        inbounds = await mz.get_inbounds()
    except Exception as e:
        log.error("get_inbounds failed: %s", e)
        raise HTTPException(502, "Marzban 后端不可达")
    inbounds_map: dict[str, list[str]] = {}
    for p in cfg["default_proxies"]:
        tags = [ib["tag"] for ib in inbounds.get(p, [])]
        if tags:
            inbounds_map[p] = tags
    if not inbounds_map:
        raise HTTPException(500, "Marzban 没有可用 inbounds")

    try:
        user = await mz.create_user(
            username=username,
            data_limit_gb=payload_data_gb,
            expire_days=payload_expire_days,
            reset_strategy=payload_reset,
            proxies=list(inbounds_map.keys()),
            inbounds_map=inbounds_map,
            note=note,
        )
    except httpx.HTTPStatusError as e:
        log.error("marzban create user failed: %s", e)
        raise HTTPException(502, f"Marzban 创建失败: {e.response.text[:200]}")
    except Exception as e:
        log.error("marzban create user failed: %s", e)
        raise HTTPException(502, f"Marzban 不可达: {e}")

    sub_url: str = user.get("subscription_url", "")
    sub_token = sub_url.rsplit("/sub/", 1)[-1].rstrip("/") if "/sub/" in sub_url else ""
    return username, sub_url, sub_token


async def _sub_brief(sub_token: str, fallback_url: str = "") -> dict:
    """组装订阅详情卡片信息（包含流量/QR）"""
    try:
        info = await mz.get_sub_info(sub_token)
    except Exception as e:
        log.warning("sub_info fail token=%s err=%s", sub_token, e)
        return {
            "subscription_token": sub_token,
            "subscription_url": fallback_url,
            "qrcode_data_url": make_qr_data_url(fallback_url) if fallback_url else "",
            "status_zh": "未知（Marzban 不可达）",
            "used_human": "—", "remaining_human": "—", "used_pct": 0,
        }
    used = int(info.get("used_traffic") or 0)
    limit = int(info.get("data_limit") or 0)
    remaining = max(0, limit - used) if limit else None
    pct = (used / limit * 100.0) if limit else 0.0
    strategy = info.get("data_limit_reset_strategy") or "no_reset"
    reset_in = next_reset_seconds(strategy)
    status_raw = info.get("status") or "unknown"
    status_zh = {
        "active": "活跃", "limited": "流量已用尽", "expired": "已过期",
        "disabled": "已禁用", "on_hold": "待激活",
    }.get(status_raw, status_raw)
    expire = info.get("expire")
    expire_in = (int(expire) - int(time.time())) if expire else None
    sub_url = info.get("subscription_url") or fallback_url
    return {
        "subscription_token": sub_token,
        "subscription_url": sub_url,
        "qrcode_data_url": make_qr_data_url(sub_url) if sub_url else "",
        "username_marzban": info.get("username"),
        "data_limit_gb": round(limit / GB, 2) if limit else 0,
        "reset_strategy": strategy,
        "status": status_raw,
        "status_zh": status_zh,
        "used_bytes": used, "limit_bytes": limit,
        "used_human": humanize_bytes(used),
        "limit_human": humanize_bytes(limit) if limit else "无限",
        "remaining_human": humanize_bytes(remaining) if remaining is not None else "无限",
        "used_pct": round(pct, 2),
        "reset_in_seconds": reset_in,
        "expire_in_seconds": expire_in,
        "expire_ts": expire,
        "created_at": info.get("created_at"),
        "online_at": info.get("online_at"),
    }


# ============================================================
# 旧版 /api/signup 兼容（不创建账户）
# ============================================================
@app.post("/api/signup")
async def signup(payload: SignupRequest, request: Request) -> dict:
    invites = load_invites()
    inv = next((i for i in invites if i.get("code") == payload.invite_code), None)
    if inv is None:
        raise HTTPException(404, "邀请码无效")
    if inv.get("used_by"):
        raise HTTPException(410, "邀请码已被使用")

    username, sub_url, sub_token = await _create_marzban_user_for_invite(
        inv, payload.data_limit_gb, payload.expire_days, payload.reset_strategy,
        payload.friend_name, f"signup invite={payload.invite_code} friend={payload.friend_name}",
    )

    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "")
    with db() as conn:
        conn.execute(
            "INSERT INTO signups(invite_code, friend_name, marzban_username, subscription_url, subscription_token, client_ip) VALUES (?,?,?,?,?,?)",
            (payload.invite_code, payload.friend_name, username, sub_url, sub_token, client_ip[:64]),
        )

    inv["used_by"] = payload.friend_name
    inv["used_at"] = now_iso()
    inv["marzban_username"] = username
    save_invites(invites)

    portal_url = f"{PORTAL_BASE_URL}/me.html?token={sub_token}"
    qr = make_qr_data_url(sub_url) if sub_url else ""

    expire_ts = int(time.time()) + payload.expire_days * 86400
    return {
        "ok": True, "username": username,
        "subscription_url": sub_url, "subscription_token": sub_token,
        "portal_url": portal_url, "qrcode_data_url": qr,
        "data_limit_gb": payload.data_limit_gb,
        "reset_strategy": payload.reset_strategy,
        "expire_days": payload.expire_days,
        "expire_at": datetime.fromtimestamp(expire_ts, tz=timezone.utc).isoformat(timespec="seconds"),
    }


# ============================================================
# 朋友账户: 注册 + 激活首个订阅
# ============================================================
@app.post("/api/account/signup")
async def account_signup(payload: AccountSignupRequest, request: Request, response: Response) -> dict:
    invites = load_invites()
    inv = next((i for i in invites if i.get("code") == payload.invite_code), None)
    if inv is None:
        raise HTTPException(404, "邀请码无效")
    if inv.get("used_by"):
        raise HTTPException(410, "邀请码已被使用")

    with db() as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE username=?", (payload.username,)).fetchone():
            raise HTTPException(409, "用户名已被占用，请换一个")

    username_mz, sub_url, sub_token = await _create_marzban_user_for_invite(
        inv, payload.data_limit_gb, payload.expire_days, payload.reset_strategy,
        payload.username, f"account_signup invite={payload.invite_code} account={payload.username}",
    )

    pw_hash = pwd_ctx.hash(payload.password)
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "")

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO accounts(username, password_hash, last_login_at) VALUES (?,?,?)",
            (payload.username, pw_hash, now_iso()),
        )
        aid = cur.lastrowid
        conn.execute(
            "INSERT INTO signups(invite_code, friend_name, marzban_username, subscription_url, subscription_token, client_ip, account_id) VALUES (?,?,?,?,?,?,?)",
            (payload.invite_code, payload.username, username_mz, sub_url, sub_token, client_ip[:64], aid),
        )

    inv["used_by"] = payload.username
    inv["used_at"] = now_iso()
    inv["marzban_username"] = username_mz
    save_invites(invites)

    set_session_cookie(response, aid, payload.username)
    qr = make_qr_data_url(sub_url) if sub_url else ""

    log.info("account signup ok username=%s invite=%s", payload.username, payload.invite_code)
    return {
        "ok": True,
        "account": {"username": payload.username, "id": aid},
        "subscription_url": sub_url,
        "subscription_token": sub_token,
        "qrcode_data_url": qr,
        "username_marzban": username_mz,
        "data_limit_gb": payload.data_limit_gb,
        "expire_days": payload.expire_days,
        "reset_strategy": payload.reset_strategy,
    }


@app.post("/api/account/login")
async def account_login(payload: AccountLoginRequest, request: Request, response: Response) -> dict:
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "0.0.0.0").split(",")[0].strip()
    check_rate_limit(ip)
    with db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE username=?", (payload.username,)).fetchone()
        if not row or not pwd_ctx.verify(payload.password, row["password_hash"]):
            record_login_fail(ip)
            raise HTTPException(401, "用户名或密码错误")
        if row["disabled"]:
            raise HTTPException(403, "账户已被禁用")
        conn.execute("UPDATE accounts SET last_login_at=? WHERE id=?", (now_iso(), row["id"]))

    set_session_cookie(response, row["id"], row["username"])
    # admin 账户登录自动写 Caddy 守门 cookie，免再输密码
    _caddy_pass = os.environ.get("CADDY_ADMIN_PASS_TOKEN", "")
    if row["username"] == "admin" and _caddy_pass:
        response.set_cookie(
            key="vpn_admin_pass", value=_caddy_pass,
            max_age=86400, secure=True, httponly=True, samesite="lax", path="/"
        )
    return {"ok": True, "account": {"id": row["id"], "username": row["username"]}}


@app.post("/api/account/logout")
async def account_logout(response: Response) -> dict:
    clear_session_cookie(response)
    response.delete_cookie("vpn_admin_pass", path="/")
    return {"ok": True}


@app.get("/api/account/me")
async def account_me(acc: dict = Depends(current_account)) -> dict:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM signups WHERE account_id=? AND deleted_at IS NULL ORDER BY id DESC", (acc["id"],)
        ).fetchall()
    subs = []
    for r in rows:
        d = await _sub_brief(r["subscription_token"] or "", r["subscription_url"] or "")
        d["is_deleted"] = bool(r["deleted_at"]); d["deleted_at"] = r["deleted_at"]; d["invite_code"] = r["invite_code"]
        d["signup_id"] = r["id"]
        d.setdefault("created_at", r["created_at"])
        subs.append(d)
    return {
        "account": {
            "id": acc["id"],
            "username": acc["username"],
            "created_at": acc["created_at"],
            "last_login_at": acc["last_login_at"],
            "subscription_count": len(subs),
        },
        "subscriptions": subs,
    }





@app.post("/api/account/subscriptions/{token}/delete")
async def account_delete_sub(token: str, acc: dict = Depends(current_account)) -> dict:
    """朋友自己删自己的订阅。删 Marzban user，signups 表标记 deleted_at (灰显)"""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM signups WHERE subscription_token=? AND account_id=?",
            (token, acc["id"])
        ).fetchone()
        if not row:
            raise HTTPException(404, "订阅不存在或不属于你")
        if row["deleted_at"]:
            return {"ok": True, "already": True}
        mz_name = row["marzban_username"]

    # 调 Marzban 删 user
    try:
        await mz.delete_user(mz_name)
    except Exception as e:
        log.warning("Marzban delete %s failed: %s", mz_name, e)
        # 即使 Marzban 报错也继续标记本地，避免 user 卡在中间态

    with db() as conn:
        conn.execute(
            "UPDATE signups SET deleted_at=? WHERE subscription_token=?",
            (now_iso(), token)
        )
    return {"ok": True, "deleted_at": now_iso()}


@app.post("/api/account/redeem")
async def account_redeem(payload: RedeemRequest, request: Request, acc: dict = Depends(current_account)) -> dict:
    invites = load_invites()
    inv = next((i for i in invites if i.get("code") == payload.invite_code), None)
    if inv is None:
        raise HTTPException(404, "邀请码无效")
    if inv.get("used_by"):
        raise HTTPException(410, "邀请码已被使用")

    username_mz, sub_url, sub_token = await _create_marzban_user_for_invite(
        inv, payload.data_limit_gb, payload.expire_days, payload.reset_strategy,
        acc["username"], f"redeem invite={payload.invite_code} account={acc['username']}",
    )

    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "")
    with db() as conn:
        conn.execute(
            "INSERT INTO signups(invite_code, friend_name, marzban_username, subscription_url, subscription_token, client_ip, account_id) VALUES (?,?,?,?,?,?,?)",
            (payload.invite_code, acc["username"], username_mz, sub_url, sub_token, client_ip[:64], acc["id"]),
        )

    inv["used_by"] = acc["username"]
    inv["used_at"] = now_iso()
    inv["marzban_username"] = username_mz
    save_invites(invites)

    return {
        "ok": True,
        "subscription_url": sub_url,
        "subscription_token": sub_token,
        "qrcode_data_url": make_qr_data_url(sub_url) if sub_url else "",
        "username_marzban": username_mz,
    }


# ============================================================
# 朋友端：自查（旧版 token 模式保留）
# ============================================================
@app.get("/api/me/{sub_token}")
async def me(sub_token: str) -> dict:
    if not re.match(r"^[A-Za-z0-9_\-]{4,128}$", sub_token):
        raise HTTPException(400, "token 格式不合法")
    try:
        info = await mz.get_sub_info(sub_token)
    except ValueError:
        raise HTTPException(404, "订阅 token 不存在")
    except Exception as e:
        log.error("sub info failed: %s", e)
        raise HTTPException(502, "Marzban 后端不可达")

    used = int(info.get("used_traffic") or 0)
    limit = int(info.get("data_limit") or 0)
    remaining = max(0, limit - used) if limit else None
    pct = (used / limit * 100.0) if limit else 0.0
    strategy = info.get("data_limit_reset_strategy") or "no_reset"
    reset_in = next_reset_seconds(strategy)
    status_raw = info.get("status") or "unknown"
    status_zh = {
        "active": "正常使用中", "limited": "流量已用尽", "expired": "订阅已过期",
        "disabled": "已被管理员禁用", "on_hold": "待激活",
    }.get(status_raw, status_raw)
    expire = info.get("expire")
    expire_in = int(expire) - int(time.time()) if expire else None
    return {
        "username": info.get("username"),
        "status": status_raw, "status_zh": status_zh,
        "used_bytes": used, "limit_bytes": limit, "remaining_bytes": remaining,
        "used_human": humanize_bytes(used),
        "limit_human": humanize_bytes(limit) if limit else "无限",
        "remaining_human": humanize_bytes(remaining) if remaining is not None else "无限",
        "used_pct": round(pct, 2),
        "reset_strategy": strategy, "reset_in_seconds": reset_in,
        "expire_ts": expire, "expire_in_seconds": expire_in,
        "subscription_url": info.get("subscription_url"),
        "created_at": info.get("created_at"),
        "online_at": info.get("online_at"),
    }


# ============================================================
# 管理员端
# ============================================================
@app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
async def admin_stats() -> dict:
    invites = load_invites()
    total_inv = len(invites)
    used_inv = sum(1 for i in invites if i.get("used_by"))
    with db() as conn:
        signup_count = conn.execute("SELECT COUNT(*) FROM signups").fetchone()[0]
        account_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    marzban_ok = False
    marzban_err = None
    try:
        await mz.get_inbounds()
        marzban_ok = True
    except Exception as e:
        marzban_err = str(e)[:200]
    return {
        "marzban_ok": marzban_ok, "marzban_error": marzban_err,
        "signup_count": signup_count,
        "account_count": account_count,
        "invite_total": total_inv,
        "invite_used": used_inv,
        "invite_unused": total_inv - used_inv,
        "invite_used_pct": round(used_inv / total_inv * 100, 1) if total_inv else 0.0,
        "allowed_reset_strategies": list(ALLOWED_RESET),
    }


@app.get("/api/admin/invites", dependencies=[Depends(require_admin)])
async def list_invites() -> dict:
    invs = load_invites()
    enriched = []
    for i in invs:
        e = dict(i)
        e["state"] = "used" if i.get("used_by") else "available"
        max_data, max_exp, min_reset = invite_caps(i)
        e["max_data_limit_gb"] = max_data
        e["max_expire_days"] = max_exp
        e["min_reset_strategy"] = min_reset
        enriched.append(e)
    enriched.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"invites": enriched, "total": len(enriched)}


@app.post("/api/admin/invites", dependencies=[Depends(require_admin)])
async def create_invites(payload: InviteCreate) -> dict:
    if payload.min_reset_strategy not in ALLOWED_RESET:
        raise HTTPException(400, f"min_reset_strategy 必须是 {ALLOWED_RESET}")
    invs = load_invites()
    existing = {i.get("code") for i in invs}
    new_codes: list[dict] = []
    for _ in range(payload.count):
        code = gen_invite_code()
        while code in existing or any(c["code"] == code for c in new_codes):
            code = gen_invite_code()
        new_codes.append({
            "code": code,
            "max_data_limit_gb": payload.max_data_limit_gb,
            "max_expire_days": payload.max_expire_days,
            "min_reset_strategy": payload.min_reset_strategy,
            "note": payload.note,
            "created_at": now_iso(),
            "used_by": None, "used_at": None, "marzban_username": None,
        })
    invs.extend(new_codes)
    save_invites(invs)
    return {"ok": True, "created": new_codes, "codes": [c["code"] for c in new_codes]}


@app.get("/api/signup/preview/{invite_code}")
async def preview_invite(invite_code: str) -> dict:
    code = invite_code.strip().upper()
    if not INVITE_RE.match(code):
        raise HTTPException(400, "邀请码格式不合法")
    invs = load_invites()
    inv = next((i for i in invs if i.get("code") == code), None)
    if inv is None:
        raise HTTPException(404, "邀请码不存在")
    if inv.get("used_by"):
        raise HTTPException(410, "邀请码已被使用")
    max_data, max_exp, min_reset = invite_caps(inv)
    return {
        "max_data_limit_gb": max_data,
        "max_expire_days": max_exp,
        "min_reset_strategy": min_reset,
        "allowed_reset_strategies": allowed_resets_for(min_reset),
        "note": inv.get("note", ""),
    }


@app.delete("/api/admin/invites/{code}", dependencies=[Depends(require_admin)])
async def delete_invite(code: str) -> dict:
    """
    删除邀请码:
    - 未用码: 直接删除
    - 已用码: 仅从 invites.json 删除该 code 记录,保留 marzban_username / signups 行 → 订阅继续有效
    """
    invs = load_invites()
    target = next((i for i in invs if i.get("code") == code), None)
    if target is None:
        raise HTTPException(404, "邀请码不存在")
    sub_preserved = bool(target.get("used_by"))
    new = [i for i in invs if i.get("code") != code]
    save_invites(new)
    return {"ok": True, "deleted": code, "sub_preserved": sub_preserved}


@app.get("/api/admin/signups", dependencies=[Depends(require_admin)])
async def list_signups(limit: int = 100) -> dict:
    """
    列出激活订阅,同时与 Marzban 同步:
    凡 signups 表 marzban_username 不在 Marzban 当前 user 列表 → 删除 orphan 行
    """
    orphans_removed = 0
    mz_users: Optional[set[str]] = None
    try:
        ml = await mz.list_users()
        mz_users = {u["username"] for u in ml.get("users", [])}
    except Exception as e:
        log.warning("list_users sync skip: %s", e)

    if mz_users is not None:
        with db() as conn:
            all_rows = conn.execute("SELECT id, marzban_username FROM signups").fetchall()
            orphan_ids = [r["id"] for r in all_rows if r["marzban_username"] not in mz_users]
            if orphan_ids:
                conn.executemany("DELETE FROM signups WHERE id=?", [(i,) for i in orphan_ids])
                orphans_removed = len(orphan_ids)
                log.info("signups sync: removed %d orphan rows", orphans_removed)

    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM signups ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),)
        ).fetchall()
    return {"signups": [dict(r) for r in rows], "total": len(rows), "orphans_removed": orphans_removed}


# ----------------- accounts admin -----------------
@app.get("/api/admin/accounts", dependencies=[Depends(require_admin)])
async def admin_accounts() -> dict:
    with db() as conn:
        accs = conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()
        out = []
        for a in accs:
            subs = conn.execute("SELECT subscription_token, subscription_url FROM signups WHERE account_id=?", (a["id"],)).fetchall()
            total_used = 0
            for s in subs:
                tok = s["subscription_token"] or ""
                if not tok:
                    continue
                try:
                    info = await mz.get_sub_info(tok)
                    total_used += int(info.get("used_traffic") or 0)
                except Exception:
                    pass
            out.append({
                "id": a["id"],
                "username": a["username"],
                "created_at": a["created_at"],
                "last_login_at": a["last_login_at"],
                "disabled": bool(a["disabled"]),
                "subscription_count": len(subs),
                "total_used_gb": round(total_used / GB, 2),
            })
        # Legacy 虚拟账户: account_id IS NULL 的 signups
        legacy_rows = conn.execute("SELECT COUNT(*) FROM signups WHERE account_id IS NULL").fetchone()
        legacy_count = legacy_rows[0] if legacy_rows else 0
    return {"accounts": out, "legacy_signups": legacy_count}


@app.get("/api/admin/accounts/{username}", dependencies=[Depends(require_admin)])
async def admin_account_detail(username: str) -> dict:
    with db() as conn:
        a = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if not a:
            raise HTTPException(404, "账户不存在")
        rows = conn.execute("SELECT * FROM signups WHERE account_id=? ORDER BY id DESC", (a["id"],)).fetchall()
    subs = []
    for r in rows:
        d = await _sub_brief(r["subscription_token"] or "", r["subscription_url"] or "")
        d["is_deleted"] = bool(r["deleted_at"]); d["deleted_at"] = r["deleted_at"]; d["invite_code"] = r["invite_code"]
        d["signup_id"] = r["id"]
        subs.append(d)
    return {
        "account": {
            "id": a["id"], "username": a["username"],
            "created_at": a["created_at"], "last_login_at": a["last_login_at"],
            "disabled": bool(a["disabled"]),
            "subscription_count": len(subs),
        },
        "subscriptions": subs,
    }


@app.post("/api/admin/accounts/{username}/disable", dependencies=[Depends(require_admin)])
async def admin_disable(username: str) -> dict:
    with db() as conn:
        a = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if not a:
            raise HTTPException(404, "账户不存在")
        conn.execute("UPDATE accounts SET disabled=1 WHERE id=?", (a["id"],))
        rows = conn.execute("SELECT marzban_username FROM signups WHERE account_id=?", (a["id"],)).fetchall()
    for r in rows:
        try:
            await mz.set_status(r["marzban_username"], "disabled")
        except Exception as e:
            log.warning("disable marzban %s err=%s", r["marzban_username"], e)
    return {"ok": True}


@app.post("/api/admin/accounts/{username}/enable", dependencies=[Depends(require_admin)])
async def admin_enable(username: str) -> dict:
    with db() as conn:
        a = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if not a:
            raise HTTPException(404, "账户不存在")
        conn.execute("UPDATE accounts SET disabled=0 WHERE id=?", (a["id"],))
        rows = conn.execute("SELECT marzban_username FROM signups WHERE account_id=?", (a["id"],)).fetchall()
    for r in rows:
        try:
            await mz.set_status(r["marzban_username"], "active")
        except Exception as e:
            log.warning("enable marzban %s err=%s", r["marzban_username"], e)
    return {"ok": True}


@app.post("/api/admin/accounts/{username}/reset-password", dependencies=[Depends(require_admin)])
async def admin_reset_pw(username: str) -> dict:
    new_pw = secrets.token_urlsafe(9)[:12]
    with db() as conn:
        a = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if not a:
            raise HTTPException(404, "账户不存在")
        conn.execute("UPDATE accounts SET password_hash=? WHERE id=?", (pwd_ctx.hash(new_pw), a["id"]))
    return {"ok": True, "temporary_password": new_pw}


@app.delete("/api/admin/accounts/{username}", dependencies=[Depends(require_admin)])
async def admin_delete_account(username: str) -> dict:
    """
    级联删除账户:
    - 删账户名下所有 signups (同时调 Marzban DELETE /api/user/{mz_user} 立即断连)
    - 删 accounts 表行
    """
    with db() as conn:
        a = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if not a:
            raise HTTPException(404, "账户不存在")
        rows = conn.execute("SELECT marzban_username FROM signups WHERE account_id=?", (a["id"],)).fetchall()
        mz_usernames = [r["marzban_username"] for r in rows]
        conn.execute("DELETE FROM signups WHERE account_id=?", (a["id"],))
        conn.execute("DELETE FROM accounts WHERE id=?", (a["id"],))

    deleted_subs = 0
    for mu in mz_usernames:
        try:
            await mz.delete_user(mu)
            deleted_subs += 1
        except Exception as e:
            log.warning("cascade del marzban %s err=%s", mu, e)
    log.info("admin deleted account=%s deleted_subs=%d", username, deleted_subs)
    return {"ok": True, "deleted_subs": deleted_subs}


# ============================================================
# 系统状态聚合 (tab-stats)
# ============================================================
import shutil as _shutil  # noqa

SERVICE_START_TS = int(time.time())


@app.get("/api/admin/system-status", dependencies=[Depends(require_admin)])
async def admin_system_status() -> dict:
    """聚合 A 项目运行状态 + B 流量 + C Top 用量"""
    # A. 项目运行状态
    invites = load_invites()
    inv_total = len(invites)
    inv_used = sum(1 for i in invites if i.get("used_by"))
    with db() as conn:
        account_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        signup_count = conn.execute("SELECT COUNT(*) FROM signups").fetchone()[0]
        sign_rows = conn.execute("SELECT marzban_username, account_id FROM signups").fetchall()
        acc_rows = conn.execute("SELECT id, username FROM accounts").fetchall()
    acc_id_to_name = {r["id"]: r["username"] for r in acc_rows}

    marzban_ok = False
    mz_users_list: list[dict] = []
    try:
        ml = await mz.list_users()
        mz_users_list = ml.get("users", [])
        marzban_ok = True
    except Exception as e:
        log.warning("system-status marzban fail: %s", e)

    by_user = {u["username"]: u for u in mz_users_list}
    # 状态细分
    status_breakdown = {"active": 0, "limited": 0, "expired": 0, "disabled": 0, "on_hold": 0, "unknown": 0}
    for u in mz_users_list:
        st = u.get("status", "unknown")
        status_breakdown[st] = status_breakdown.get(st, 0) + 1

    # B. 流量
    monthly_used_bytes = 0  # 项目级累计 — 用 daemon 持久化
    history_total_bytes = 0
    rx_mbps = tx_mbps = 0.0
    # 复用 daemon 写的 current.json + user-traffic.json
    try:
        cur_path = Path("/portal-stats/current.json")
        if cur_path.exists():
            cur = json.loads(cur_path.read_text())
            rx_mbps = float(cur.get("traffic", {}).get("rx_mbps", 0))
            tx_mbps = float(cur.get("traffic", {}).get("tx_mbps", 0))
    except Exception:
        pass

    try:
        traf_total_path = Path("/portal-stats/traffic-total.json")
        if traf_total_path.exists():
            tt = json.loads(traf_total_path.read_text())
            monthly_used_bytes = int(tt.get("monthly_bytes", 0))
            history_total_bytes = int(tt.get("lifetime_bytes", 0))
    except Exception:
        pass

    # C. Top 用量
    # 订阅 top
    sub_usage = []
    name_to_account = {}
    for r in sign_rows:
        name_to_account[r["marzban_username"]] = acc_id_to_name.get(r["account_id"], "—")
    for mu, mz_u in by_user.items():
        used = int(mz_u.get("used_traffic") or 0)
        sub_usage.append({
            "marzban_username": mu,
            "account": name_to_account.get(mu, "—"),
            "used_bytes": used,
            "used_gb": round(used / GB, 3),
            "limit_bytes": int(mz_u.get("data_limit") or 0),
            "status": mz_u.get("status", "unknown"),
        })
    sub_usage.sort(key=lambda x: x["used_bytes"], reverse=True)
    top_subs = sub_usage[:5]

    # 账户 top
    acc_agg: dict[str, dict] = {}
    for r in sign_rows:
        if r["account_id"] is None:
            continue
        name = acc_id_to_name.get(r["account_id"])
        if not name:
            continue
        u = by_user.get(r["marzban_username"])
        used = int(u.get("used_traffic") or 0) if u else 0
        agg = acc_agg.setdefault(name, {"username": name, "subscription_count": 0, "used_bytes": 0})
        agg["subscription_count"] += 1
        agg["used_bytes"] += used
    acc_list = list(acc_agg.values())
    for a in acc_list:
        a["used_gb"] = round(a["used_bytes"] / GB, 3)
    acc_list.sort(key=lambda x: x["used_bytes"], reverse=True)
    top_accounts = acc_list[:5]

    uptime_seconds = int(time.time()) - SERVICE_START_TS
    monthly_cap_tb = 20
    monthly_cap_bytes = monthly_cap_tb * (1024 ** 4)
    monthly_pct = round(monthly_used_bytes / monthly_cap_bytes * 100, 2) if monthly_cap_bytes else 0

    return {
        "running": {
            "marzban_ok": marzban_ok,
            "account_count": account_count,
            "signup_count": signup_count,
            "active_subs": status_breakdown.get("active", 0),
            "status_breakdown": status_breakdown,
            "invite_total": inv_total,
            "invite_used": inv_used,
            "invite_unused": inv_total - inv_used,
            "invite_used_pct": round(inv_used / inv_total * 100, 1) if inv_total else 0,
            "uptime_seconds": uptime_seconds,
        },
        "traffic": {
            "rx_mbps": rx_mbps,
            "tx_mbps": tx_mbps,
            "monthly_used_bytes": monthly_used_bytes,
            "monthly_used_gb": round(monthly_used_bytes / GB, 2),
            "monthly_used_tb": round(monthly_used_bytes / (1024 ** 4), 4),
            "monthly_cap_tb": monthly_cap_tb,
            "monthly_used_pct": monthly_pct,
            "lifetime_total_bytes": history_total_bytes,
            "lifetime_total_gb": round(history_total_bytes / GB, 2),
            "lifetime_total_tb": round(history_total_bytes / (1024 ** 4), 4),
        },
        "top_subs": top_subs,
        "top_accounts": top_accounts,
    }


@app.get("/api/admin/user-traffic-history", dependencies=[Depends(require_admin)])
async def admin_user_traffic_history() -> dict:
    """读 daemon 写的 user-traffic.json,返回各 marzban_username 最近 300 个采样"""
    path = Path("/portal-stats/user-traffic.json")
    if not path.exists():
        return {"users": {}, "samples": 0, "note": "daemon 尚未写入 user-traffic.json"}
    try:
        data = json.loads(path.read_text())
        return data
    except Exception as e:
        return {"users": {}, "samples": 0, "note": f"读取失败: {e}"}


@app.delete("/api/admin/subscription/{token}", dependencies=[Depends(require_admin)])
async def admin_delete_sub(token: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM signups WHERE subscription_token=?", (token,)).fetchone()
        if not row:
            raise HTTPException(404, "订阅不存在")
        mz_user = row["marzban_username"]
        conn.execute("DELETE FROM signups WHERE id=?", (row["id"],))
    try:
        await mz.delete_user(mz_user)
    except Exception as e:
        log.warning("delete marzban %s err=%s", mz_user, e)
    return {"ok": True, "deleted_marzban_user": mz_user}
