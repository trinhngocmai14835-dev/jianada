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
from typing import Any, Callable, Dict, Optional

from core.process_env import clean_subprocess_context, sanitized_subprocess_env
from core.version import APP_EXE_NAME, APP_VERSION, UPDATE_ALLOWED_HOSTS, UPDATE_MANIFEST_URL

_CHUNK_SIZE = 1024 * 1024
_TASK_EXIT_DELAY = 1.5
_INSTALL_LOCK = threading.Lock()
_INSTALL_STATE: Dict[str, Any] = {}


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _initial_install_state() -> Dict[str, Any]:
    return {
        "ok": True,
        "running": False,
        "phase": "idle",
        "percent": 0,
        "message": "",
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "current_version": APP_VERSION,
        "latest_version": "",
        "updated_at": _timestamp(),
    }


def _set_install_state(**fields) -> Dict[str, Any]:
    with _INSTALL_LOCK:
        if not _INSTALL_STATE:
            _INSTALL_STATE.update(_initial_install_state())
        _INSTALL_STATE.update(fields)
        _INSTALL_STATE["updated_at"] = _timestamp()
        return dict(_INSTALL_STATE)


def get_update_install_status() -> Dict[str, Any]:
    with _INSTALL_LOCK:
        if not _INSTALL_STATE:
            _INSTALL_STATE.update(_initial_install_state())
        return dict(_INSTALL_STATE)


def _report(progress_callback: Optional[Callable[..., None]], **fields) -> None:
    if progress_callback:
        progress_callback(**fields)


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
    data = json.loads(raw.decode("utf-8-sig"))
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


def _download_file(url: str, dest: Path, progress_callback: Optional[Callable[..., None]] = None) -> None:
    safe_url = _validate_update_url(url)
    req = urllib.request.Request(safe_url, headers={"User-Agent": f"AutoBetPro/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        try:
            total = int(resp.headers.get("Content-Length") or 0)
        except Exception:
            total = 0
        downloaded = 0
        _report(
            progress_callback,
            phase="downloading",
            percent=8,
            message="正在下载更新包...",
            downloaded_bytes=downloaded,
            total_bytes=total,
        )
        while True:
            chunk = resp.read(_CHUNK_SIZE)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            percent = 8
            if total > 0:
                percent = min(80, 8 + int(downloaded * 72 / total))
            _report(
                progress_callback,
                phase="downloading",
                percent=percent,
                message="正在下载更新包...",
                downloaded_bytes=downloaded,
                total_bytes=total,
            )


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
    app_process_name = current_exe.stem
    expected_exe_hash = _sha256(new_exe)
    body = f"""
$ErrorActionPreference = 'Stop'
$pidToWait = {os.getpid()}
$src = {_ps_quote(new_exe)}
$dst = {_ps_quote(current_exe)}
$backup = {_ps_quote(backup)}
$log = {_ps_quote(log)}
$appExeName = {_ps_quote(current_exe.name)}
$appProcessName = {_ps_quote(app_process_name)}
$expectedHash = '{expected_exe_hash.upper()}'
$maxAttempts = 90
$retryDelayMs = 1000
$graceSeconds = 8
$forceWaitSeconds = 45
function Write-UpdateLog($msg) {{
  Add-Content -LiteralPath $log -Value ("$(Get-Date -Format s) " + $msg) -Encoding UTF8
}}
function Clear-PyInstallerEnv {{
  foreach ($item in Get-ChildItem Env: -ErrorAction SilentlyContinue) {{
    if ($item.Name -eq "_MEIPASS2" -or $item.Name -like "_PYI*" -or $item.Name -like "PYINSTALLER_*") {{
      Remove-Item -LiteralPath ("Env:" + $item.Name) -ErrorAction SilentlyContinue
    }}
  }}
  $env:PYINSTALLER_RESET_ENVIRONMENT = "1"
}}
function Get-AppProcessIds {{
  $items = @()
  foreach ($p in Get-Process -ErrorAction SilentlyContinue) {{
    try {{
      if ($p.Id -eq $PID) {{ continue }}
      if ($p.Path -eq $dst) {{ $items += $p.Id; continue }}
    }} catch {{
      try {{
        if ($p.ProcessName -eq $appProcessName) {{ $items += $p.Id }}
      }} catch {{}}
    }}
  }}
  try {{
    foreach ($p in Get-CimInstance Win32_Process -ErrorAction SilentlyContinue) {{
      if ($p.ProcessId -eq $PID) {{ continue }}
      if ($p.ExecutablePath -eq $dst -or $p.Name -eq $appExeName) {{ $items += [int]$p.ProcessId }}
    }}
  }} catch {{}}
  return @($items | Select-Object -Unique)
}}
function Show-UpdateError($msg) {{
  try {{
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($msg, "自动更新失败") | Out-Null
  }} catch {{}}
}}
function Close-AppBrowserWindows {{
  foreach ($name in @("chrome", "msedge")) {{
    foreach ($p in Get-Process -Name $name -ErrorAction SilentlyContinue) {{
      try {{
        $title = [string]$p.MainWindowTitle
        if ($title -match "localhost:8080|127\\.0\\.0\\.1:8080|自动下单系统") {{
          Write-UpdateLog ("closing browser window " + $p.Id + " " + $title)
          $null = $p.CloseMainWindow()
        }}
      }} catch {{}}
    }}
  }}
}}
function Restore-BackupIfNeeded {{
  if (!(Test-Path -LiteralPath $dst) -and (Test-Path -LiteralPath $backup)) {{
    Move-Item -LiteralPath $backup -Destination $dst -Force
  }}
}}
function Stop-OldAppProcesses {{
  $ids = @(Get-AppProcessIds | Where-Object {{ $_ -ne $PID }})
  foreach ($id in $ids) {{
    try {{
      Write-UpdateLog ("force stopping app process " + $id)
      Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }} catch {{}}
  }}
}}
function Replace-Executable {{
  $dir = Split-Path -Parent $dst
  $tmp = Join-Path $dir ((Split-Path -Leaf $dst) + ".new")
  if (Test-Path -LiteralPath $tmp) {{ Remove-Item -LiteralPath $tmp -Force }}
  Copy-Item -LiteralPath $src -Destination $tmp -Force
  $tmpHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $tmp).Hash
  if ($tmpHash -ne $expectedHash) {{
    throw "new exe hash mismatch: $tmpHash"
  }}
  if (Test-Path -LiteralPath $backup) {{ Remove-Item -LiteralPath $backup -Force }}
  if (Test-Path -LiteralPath $dst) {{
    Move-Item -LiteralPath $dst -Destination $backup -Force
  }}
  Move-Item -LiteralPath $tmp -Destination $dst -Force
  $dstHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $dst).Hash
  if ($dstHash -ne $expectedHash) {{
    Restore-BackupIfNeeded
    throw "installed exe hash mismatch: $dstHash"
  }}
}}
try {{
  Write-UpdateLog "waiting for old process"
  $waitStarted = Get-Date
  while ((Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) -and (((Get-Date) - $waitStarted).TotalSeconds -lt $graceSeconds)) {{
    Start-Sleep -Milliseconds 500
  }}
  if (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) {{
    Write-UpdateLog ("old process did not exit in " + $graceSeconds + " seconds, forcing pid " + $pidToWait)
    Stop-Process -Id $pidToWait -Force -ErrorAction SilentlyContinue
  }}

  $forceStarted = Get-Date
  while ((@(Get-AppProcessIds | Where-Object {{ $_ -ne $PID }})).Count -gt 0) {{
    Stop-OldAppProcesses
    if (((Get-Date) - $forceStarted).TotalSeconds -ge $forceWaitSeconds) {{
      throw "old app process still running after $forceWaitSeconds seconds"
    }}
    Start-Sleep -Milliseconds 500
  }}
  Start-Sleep -Milliseconds 1200

  for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {{
    try {{
      Replace-Executable
      Write-UpdateLog "replace complete on attempt $attempt"
      Start-Sleep -Milliseconds 500
      $dir = Split-Path -Parent $dst
      Clear-PyInstallerEnv
      Start-Process -FilePath $dst -WorkingDirectory $dir
      exit 0
    }} catch {{
      Restore-BackupIfNeeded
      Write-UpdateLog ("replace attempt " + $attempt + " failed: " + $_.Exception.Message)
      Start-Sleep -Milliseconds $retryDelayMs
    }}
  }}
  throw "replace failed after $maxAttempts attempts"
}} catch {{
  $msg = "更新替换失败，请关闭所有 自动下单系统Pro 进程后重新打开旧版本再点更新。" + [Environment]::NewLine + $_.Exception.Message
  Write-UpdateLog ("replace failed: " + $_.Exception.Message)
  Show-UpdateError $msg
  if ((@(Get-AppProcessIds | Where-Object {{ $_ -ne $PID }})).Count -eq 0) {{
    Clear-PyInstallerEnv
    if (Test-Path -LiteralPath $dst) {{ Start-Process -FilePath $dst -WorkingDirectory (Split-Path -Parent $dst) }}
    elseif (Test-Path -LiteralPath $backup) {{ Start-Process -FilePath $backup -WorkingDirectory (Split-Path -Parent $backup) }}
  }}
}}
""".lstrip()
    script.write_text(body, encoding="utf-8-sig")
    return script


def _detached_creationflags() -> int:
    flags = 0
    for name in ("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
        flags |= int(getattr(subprocess, name, 0) or 0)
    return flags


def _launch_helper_script(helper: Path, update_root: Path) -> None:
    with clean_subprocess_context():
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(helper)],
            cwd=str(update_root),
            env=sanitized_subprocess_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=_detached_creationflags(),
        )


def _spawn_exit_watchdog(pid: int, delay_seconds: float = 8) -> bool:
    if os.name != "nt":
        return False
    delay = max(2, int(round(delay_seconds)))
    script = (
        f"Start-Sleep -Seconds {delay}; "
        f"try {{ Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue }} catch {{}}"
    )
    with clean_subprocess_context():
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", script],
            env=sanitized_subprocess_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=_detached_creationflags(),
        )
    return True

def _exit_current_process_later() -> None:
    time.sleep(_TASK_EXIT_DELAY)
    os._exit(0)


def _schedule_current_process_exit() -> None:
    _spawn_exit_watchdog(os.getpid(), _TASK_EXIT_DELAY + 8)
    threading.Thread(target=_exit_current_process_later, daemon=True).start()

def prepare_update_install(progress_callback: Optional[Callable[..., None]] = None) -> Dict[str, Any]:
    _report(progress_callback, phase="checking", percent=2, message="正在检查更新...", current_version=APP_VERSION)
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
    _report(
        progress_callback,
        phase="ready",
        percent=5,
        message="已发现新版本，准备下载...",
        current_version=APP_VERSION,
        latest_version=latest_version,
    )
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
        _download_file(url, zip_path, progress_callback)
        _report(progress_callback, phase="verifying", percent=84, message="正在校验更新包...", latest_version=latest_version)
        actual_hash = _sha256(zip_path)
        if actual_hash != expected_hash:
            return {
                "ok": False,
                "message": "更新包校验失败，请稍后重试",
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
            }
        _report(progress_callback, phase="extracting", percent=90, message="正在解压更新包...", latest_version=latest_version)
        _safe_extract(zip_path, extract_dir)
        _report(progress_callback, phase="preparing", percent=96, message="正在准备替换程序...", latest_version=latest_version)
        new_exe = _find_update_exe(extract_dir)
        helper = _write_helper_script(update_root, new_exe, current_exe)
        _launch_helper_script(helper, update_root)
        _report(progress_callback, phase="restarting", percent=100, message="更新包已下载，软件即将关闭并自动重启", latest_version=latest_version)
        _schedule_current_process_exit()
        return {
            "ok": True,
            "message": "更新包已下载，软件即将关闭并自动重启",
            "current_version": APP_VERSION,
            "latest_version": latest_version,
        }
    except Exception as exc:
        return {"ok": False, "message": f"安装更新失败：{exc}"}


def _run_update_install_job() -> None:
    def _progress(**fields):
        _set_install_state(ok=True, running=True, **fields)

    try:
        result = prepare_update_install(_progress)
        if result.get("ok"):
            _set_install_state(
                ok=True,
                running=True,
                phase="restarting",
                percent=100,
                message=result.get("message") or "更新包已下载，软件即将关闭并自动重启",
                current_version=result.get("current_version", APP_VERSION),
                latest_version=result.get("latest_version") or get_update_install_status().get("latest_version") or "",
            )
        else:
            last = get_update_install_status()
            _set_install_state(
                ok=False,
                running=False,
                phase="failed",
                percent=last.get("percent", 0),
                message=result.get("message") or "更新失败",
                current_version=result.get("current_version", APP_VERSION),
                latest_version=result.get("latest_version") or last.get("latest_version") or "",
            )
    except Exception as exc:
        last = get_update_install_status()
        _set_install_state(
            ok=False,
            running=False,
            phase="failed",
            percent=last.get("percent", 0),
            message=f"安装更新失败：{exc}",
            current_version=APP_VERSION,
            latest_version=last.get("latest_version") or "",
        )


def start_update_install() -> Dict[str, Any]:
    if not getattr(sys, "frozen", False):
        return {"ok": False, "message": "当前不是打包后的 EXE，无法自动替换更新", "status": get_update_install_status()}

    with _INSTALL_LOCK:
        if not _INSTALL_STATE:
            _INSTALL_STATE.update(_initial_install_state())
        if _INSTALL_STATE.get("running"):
            return {"ok": True, "started": False, "message": "更新正在进行中", "status": dict(_INSTALL_STATE)}
        _INSTALL_STATE.clear()
        _INSTALL_STATE.update(_initial_install_state())
        _INSTALL_STATE.update({
            "ok": True,
            "running": True,
            "phase": "queued",
            "percent": 0,
            "message": "更新任务已开始，请不要关闭软件...",
            "updated_at": _timestamp(),
        })
        status = dict(_INSTALL_STATE)

    thread = threading.Thread(target=_run_update_install_job, daemon=True, name="update-install")
    thread.start()
    return {"ok": True, "started": True, "message": "已开始下载更新包，请不要关闭软件", "status": status}
