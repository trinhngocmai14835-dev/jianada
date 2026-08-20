import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict

from core.version import APP_EXE_NAME, APP_VERSION, UPDATE_ALLOWED_HOSTS, UPDATE_MANIFEST_URL

_CHUNK_SIZE = 1024 * 1024
_TASK_EXIT_DELAY = 1.0


def _version_key(version: str) -> tuple:
    parts = re.findall(r"\d+", str(version or ""))
    return tuple(int(p) for p in parts)


def _is_newer(remote_version: str, current_version: str) -> bool:
    remote = _version_key(remote_version)
    current = _version_key(current_version)
    if remote and current:
        width = max(len(remote), len(current))
        remote = remote + (0,) * (width - len(remote))
        current = current + (0,) * (width - len(current))
        return remote > current
    return bool(remote_version) and str(remote_version) != str(current_version)


def _manifest_url() -> str:
    sep = "&" if "?" in UPDATE_MANIFEST_URL else "?"
    return f"{UPDATE_MANIFEST_URL}{sep}t={int(time.time())}"


def _validate_update_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in UPDATE_ALLOWED_HOSTS:
        raise ValueError("更新包下载地址不在可信范围内")
    if not parsed.path.lower().startswith("/releases/"):
        raise ValueError("更新包路径不在 releases 目录")
    return urllib.parse.urlunparse(parsed)


def _public_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    allowed = ("version", "url", "sha256", "notes", "force")
    result = {k: manifest.get(k) for k in allowed if k in manifest}
    if result.get("url"):
        result["url"] = _validate_update_url(str(result["url"]))
    return result


def fetch_update_manifest(timeout: int = 8) -> Dict[str, Any]:
    req = urllib.request.Request(
        _manifest_url(),
        headers={
            "User-Agent": f"AutoBetPro/{APP_VERSION}",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(1024 * 1024)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("更新配置格式不正确")
    _public_manifest(data)
    return data


def check_for_update() -> Dict[str, Any]:
    try:
        manifest = fetch_update_manifest()
        latest_version = str(manifest.get("version") or "")
        download_url = str(manifest.get("url") or "")
        if not latest_version or not download_url:
            return {
                "ok": False,
                "message": "更新配置缺少版本号或下载地址",
                "current_version": APP_VERSION,
            }
        has_update = _is_newer(latest_version, APP_VERSION)
        return {
            "ok": True,
            "current_version": APP_VERSION,
            "latest_version": latest_version,
            "has_update": has_update,
            "manifest": _public_manifest(manifest),
            "notes": manifest.get("notes") or "",
            "force": bool(manifest.get("force", False)),
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"检查更新失败：{exc}",
            "current_version": APP_VERSION,
        }


def _download_file(url: str, dest: Path) -> None:
    safe_url = _validate_update_url(url)
    req = urllib.request.Request(safe_url, headers={"User-Agent": f"AutoBetPro/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out, length=_CHUNK_SIZE)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(zip_path: Path, extract_dir: Path) -> None:
    root = extract_dir.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad:
            raise ValueError(f"更新包损坏：{bad}")
        for info in zf.infolist():
            target = (extract_dir / info.filename).resolve()
            if target != root and not str(target).lower().startswith(str(root).lower() + os.sep):
                raise ValueError("更新包包含非法路径")
            zf.extract(info, extract_dir)


def _find_update_exe(extract_dir: Path) -> Path:
    exact = [p for p in extract_dir.rglob("*.exe") if p.name == APP_EXE_NAME]
    candidates = exact or list(extract_dir.rglob("*.exe"))
    if not candidates:
        raise FileNotFoundError("更新包里没有 EXE 文件")
    return max(candidates, key=lambda p: p.stat().st_size)


def _ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write_helper_script(update_dir: Path, new_exe: Path, current_exe: Path) -> Path:
    script = update_dir / "apply_update.ps1"
    backup = current_exe.with_suffix(current_exe.suffix + ".bak")
    log = update_dir / "update.log"
    body = f"""
$ErrorActionPreference = 'Stop'
$pidToWait = {os.getpid()}
$src = {_ps_quote(new_exe)}
$dst = {_ps_quote(current_exe)}
$backup = {_ps_quote(backup)}
$log = {_ps_quote(log)}
function Write-UpdateLog($msg) {{
  Add-Content -LiteralPath $log -Value ("$(Get-Date -Format s) " + $msg) -Encoding UTF8
}}
try {{
  Write-UpdateLog "waiting for old process"
  while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) {{ Start-Sleep -Milliseconds 500 }}
  Start-Sleep -Milliseconds 800
  if (Test-Path -LiteralPath $dst) {{ Copy-Item -LiteralPath $dst -Destination $backup -Force }}
  Copy-Item -LiteralPath $src -Destination $dst -Force
  Write-UpdateLog "replace complete"
  Start-Process -FilePath $dst
}} catch {{
  Write-UpdateLog ("replace failed: " + $_.Exception.Message)
  if (Test-Path -LiteralPath $dst) {{ Start-Process -FilePath $dst }}
}}
""".lstrip()
    script.write_text(body, encoding="utf-8")
    return script


def _exit_current_process_later() -> None:
    time.sleep(_TASK_EXIT_DELAY)
    os._exit(0)


def prepare_update_install() -> Dict[str, Any]:
    if not getattr(sys, "frozen", False):
        return {"ok": False, "message": "当前不是打包后的 EXE，无法自动替换更新"}

    check = check_for_update()
    if not check.get("ok"):
        return check
    if not check.get("has_update"):
        return {
            "ok": False,
            "message": "当前已经是最新版本",
            "current_version": APP_VERSION,
            "latest_version": check.get("latest_version"),
        }

    manifest = check.get("manifest") or {}
    url = str(manifest.get("url") or "")
    expected_hash = str(manifest.get("sha256") or "").lower().strip()
    latest_version = str(manifest.get("version") or check.get("latest_version") or "latest")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash or ""):
        return {"ok": False, "message": "更新配置缺少有效 sha256，已取消自动更新"}

    current_exe = Path(sys.executable).resolve()
    update_root = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "AutoBetPro" / "updates" / latest_version
    download_dir = update_root / "download"
    extract_dir = update_root / "extract"
    shutil.rmtree(download_dir, ignore_errors=True)
    shutil.rmtree(extract_dir, ignore_errors=True)
    download_dir.mkdir(parents=True, exist_ok=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    zip_path = download_dir / "update.zip"
    try:
        _download_file(url, zip_path)
        actual_hash = _sha256(zip_path)
        if actual_hash != expected_hash:
            return {
                "ok": False,
                "message": "更新包校验失败，请稍后重试",
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
            }
        _safe_extract(zip_path, extract_dir)
        new_exe = _find_update_exe(extract_dir)
        helper = _write_helper_script(update_root, new_exe, current_exe)
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
            cwd=str(update_root),
            close_fds=True,
            creationflags=creationflags,
        )
        threading.Thread(target=_exit_current_process_later, daemon=True).start()
        return {
            "ok": True,
            "message": "更新包已下载，软件即将关闭并自动重启",
            "current_version": APP_VERSION,
            "latest_version": latest_version,
        }
    except Exception as exc:
        return {"ok": False, "message": f"安装更新失败：{exc}"}