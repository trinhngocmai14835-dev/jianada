"""Online account whitelist checks.

This is intentionally small: the server owns the allow-list, the desktop app
keeps a last-good cache, and every task start checks configured login accounts.
"""
import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime

from core.db import get_config, set_config
from core.license import get_machine_id

try:
    import certifi
except Exception:
    certifi = None


ACCOUNT_WHITELIST_BASE_URL = os.getenv(
    "ACCOUNT_WHITELIST_BASE_URL",
    "https://pub-465f078b4f484662b30eb39d27ae5155.r2.dev/account-whitelist",
)
CACHE_KEY = "account_whitelist_cache"
HTTP_TIMEOUT = 8

def _ssl_context():
    if certifi is None:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())

def normalize_account(value) -> str:
    return str(value or "").strip().lower()


def _clean_accounts(values):
    seen = set()
    out = []
    for item in values or []:
        name = normalize_account(item)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _fetch_remote_status(machine_id: str) -> dict:
    if not ACCOUNT_WHITELIST_BASE_URL:
        raise RuntimeError("账号白名单地址未配置")

    url = ACCOUNT_WHITELIST_BASE_URL.rstrip("/") + f"/{machine_id}.json"
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "autobet-pro/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=_ssl_context()) as res:
            data = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            data = {
                "machine_id": machine_id,
                "enabled": True,
                "accounts": [],
                "message": "no accounts configured",
            }
        else:
            raise

    return {
        "ok": True,
        "machine_id": machine_id,
        "customer_id": str(data.get("customer_id") or ""),
        "enabled": data.get("enabled") is not False,
        "accounts": _clean_accounts(data.get("accounts") or []),
        "source": "remote",
        "message": str(data.get("message") or "ok"),
        "updated_at": str(data.get("updated_at") or ""),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _cache_status(status: dict):
    set_config(CACHE_KEY, status)


def _cached_status(machine_id: str):
    cached = get_config(CACHE_KEY, {}) or {}
    if cached.get("machine_id") != machine_id:
        return None
    cached = dict(cached)
    cached["accounts"] = _clean_accounts(cached.get("accounts") or [])
    cached["source"] = "cache"
    return cached


def get_account_whitelist_status() -> dict:
    machine_id = get_machine_id()
    try:
        status = _fetch_remote_status(machine_id)
        _cache_status(status)
        return status
    except Exception as exc:
        cached = _cached_status(machine_id)
        if cached:
            cached["ok"] = True
            cached["message"] = f"联网白名单获取失败，已使用本机缓存：{exc}"
            return cached
        return {
            "ok": False,
            "machine_id": machine_id,
            "customer_id": "",
            "enabled": False,
            "accounts": [],
            "source": "none",
            "message": f"联网白名单获取失败：{exc}",
            "updated_at": "",
            "fetched_at": "",
        }


def check_accounts_allowed(accounts) -> tuple:
    status = get_account_whitelist_status()
    if not status.get("ok"):
        return False, status.get("message") or "账号白名单检查失败", status
    if status.get("enabled") is False:
        return False, "本机账号白名单已被停用，请联系管理员", status

    configured = [normalize_account(a) for a in (accounts or [])]
    if not configured or any(not a for a in configured):
        return False, "请先填写账号密码，空账号不允许启动", status

    allowed = set(_clean_accounts(status.get("accounts") or []))
    if not allowed:
        return False, "本机暂无授权账号，请联系管理员添加账号白名单", status

    missing = [a for a in configured if a not in allowed]
    if missing:
        return False, "以下账号未授权：" + ", ".join(sorted(set(missing))), status

    source = "远程" if status.get("source") == "remote" else "缓存"
    return True, f"账号白名单检查通过（{source}）", status
