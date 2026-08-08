"""Manage R2 account whitelist JSON files.

Usage:
  python tools/account_whitelist_admin.py get <machine_id>
  python tools/account_whitelist_admin.py add <machine_id> <account> [account...]
  python tools/account_whitelist_admin.py remove <machine_id> <account> [account...]
  python tools/account_whitelist_admin.py set <machine_id> <account> [account...]

It uploads JSON to:
  pro-downloads/account-whitelist/<machine_id>.json

The customer's software reads the public R2 URL, but only your Cloudflare login
can write/update these files.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUCKET = os.getenv("ACCOUNT_WHITELIST_BUCKET", "pro-downloads")
BASE_URL = os.getenv(
    "ACCOUNT_WHITELIST_BASE_URL",
    "https://pub-465f078b4f484662b30eb39d27ae5155.r2.dev/account-whitelist",
).rstrip("/")


def _machine_id(value):
    mid = (value or "").strip().upper()
    if len(mid) != 16 or not mid.isalnum():
        raise SystemExit("machine_id must be 16 letters/digits")
    return mid


def _account(value):
    return str(value or "").strip().lower()


def _clean_accounts(values):
    seen = set()
    out = []
    for item in values or []:
        name = _account(item)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _empty_record(mid):
    return {"machine_id": mid, "enabled": True, "accounts": []}


def _download(mid):
    url = f"{BASE_URL}/{mid}.json"
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "autobet-pro/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return _empty_record(mid)
        raise SystemExit(f"读取 R2 白名单失败：HTTP {exc.code} {exc.reason}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"读取 R2 白名单失败，已停止，避免覆盖旧账号：{exc}")


def _npx_command():
    cmd = shutil.which("npx.cmd") or shutil.which("npx")
    if not cmd:
        raise RuntimeError("未找到 npx，请先安装 Node.js，或确认 npx 已加入 PATH。")
    return cmd


def _upload(mid, record):
    record = {
        "machine_id": mid,
        "customer_id": str(record.get("customer_id") or ""),
        "enabled": record.get("enabled") is not False,
        "accounts": _clean_accounts(record.get("accounts") or []),
        "remark": str(record.get("remark") or ""),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".json") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write(chr(10))
        tmp = f.name
    try:
        object_path = f"{BUCKET}/account-whitelist/{mid}.json"
        subprocess.run(
            [_npx_command(), "--yes", "wrangler", "r2", "object", "put", object_path, "--file", tmp, "--remote"],
            cwd=ROOT,
            check=True,
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return record


def main(argv):
    if len(argv) < 3 or argv[1] not in {"get", "add", "remove", "set"}:
        print(__doc__.strip())
        return 2

    cmd = argv[1]
    mid = _machine_id(argv[2])
    record = _download(mid)
    record["machine_id"] = mid

    if cmd == "get":
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if cmd == "set":
        record["accounts"] = _clean_accounts(argv[3:])
    else:
        if len(argv) < 4:
            raise SystemExit(f"{cmd} requires at least one account")
        accounts = set(_clean_accounts(record.get("accounts") or []))
        for item in argv[3:]:
            account = _account(item)
            if not account:
                continue
            if cmd == "remove":
                accounts.discard(account)
            else:
                accounts.add(account)
        record["accounts"] = sorted(accounts)

    result = _upload(mid, record)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
