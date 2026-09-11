"""Updater safety checks."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import updater
from core import process_env


def check(cond, msg):
    if not cond:
        raise AssertionError("FAIL: " + msg)
    print("  OK " + msg)


def test_version_compare():
    print("[1] version compare")
    check(updater._is_newer("2026.08.20.3", "2026.08.20.2") is True, "newer patch accepted")
    check(updater._is_newer("2026.08.20.2", "2026.08.20.2") is False, "same version ignored")
    check(updater._is_newer("2026.08.19.9", "2026.08.20.2") is False, "older version ignored")


def test_trusted_update_url():
    print("[2] trusted update URL")
    good = "https://pub-465f078b4f484662b30eb39d27ae5155.r2.dev/releases/app.zip"
    check(updater._validate_update_url(good) == good, "fixed R2 release URL accepted")
    try:
        updater._validate_update_url("https://example.com/releases/app.zip")
    except ValueError:
        check(True, "foreign host rejected")
    else:
        raise AssertionError("FAIL: foreign host rejected")
    try:
        updater._validate_update_url("http://pub-465f078b4f484662b30eb39d27ae5155.r2.dev/releases/app.zip")
    except ValueError:
        check(True, "non-HTTPS URL rejected")
    else:
        raise AssertionError("FAIL: non-HTTPS URL rejected")


def test_manifest_accepts_utf8_bom():
    print("[3] manifest BOM tolerance")
    old_urlopen = updater.urllib.request.urlopen

    class DummyResp:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            body = {
                "version": "2026.09.03.1",
                "url": "https://pub-465f078b4f484662b30eb39d27ae5155.r2.dev/releases/app.zip",
                "sha256": "0" * 64,
            }
            return ("\ufeff" + json_dumps(body)).encode("utf-8")

    try:
        updater.urllib.request.urlopen = lambda *_args, **_kwargs: DummyResp()
        data = updater.fetch_update_manifest()
        check(data["version"] == "2026.09.03.1", "UTF-8 BOM manifest is parsed")
    finally:
        updater.urllib.request.urlopen = old_urlopen


def json_dumps(value):
    import json
    return json.dumps(value)


def test_non_frozen_install_blocked():
    print("[4] non-frozen install blocked")
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", None)
    try:
        if had_frozen:
            delattr(sys, "frozen")
        res = updater.prepare_update_install()
        check(res["ok"] is False and "EXE" in res["message"], "source runtime cannot replace executable")
    finally:
        if had_frozen:
            sys.frozen = old_frozen




def test_helper_script_forces_old_process_and_restarts_from_app_dir():
    print("[5] helper script process takeover")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        current = root / updater.APP_EXE_NAME
        new_exe = root / "extract" / updater.APP_EXE_NAME
        new_exe.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"old")
        new_exe.write_bytes(b"new")

        script = updater._write_helper_script(root, new_exe, current)
        body = script.read_text(encoding="utf-8-sig")
        check("Stop-Process -Id $pidToWait -Force" in body, "current process is force-stopped when graceful exit stalls")
        check("function Stop-OldAppProcesses" in body, "same executable processes are guarded")
        check("function Close-AppBrowserWindows" in body, "localhost browser window is asked to close during update")
        check("function Clear-PyInstallerEnv" in body, "helper clears inherited PyInstaller environment")
        check('_PYI*' in body and 'PYINSTALLER_*' in body, "helper removes all PyInstaller runtime env names")
        check("PYINSTALLER_RESET_ENVIRONMENT" in body, "new app is launched as a fresh PyInstaller top-level process")
        check("Get-CimInstance Win32_Process" in body, "old app process detection has a CIM fallback")
        check("Start-Process -FilePath $dst -WorkingDirectory $dir" in body, "new app restarts from its install directory")


def test_helper_process_is_hidden_and_detached():
    print("[6] detached helper launcher")
    calls = []
    old_popen = updater.subprocess.Popen
    saved_flags = {name: getattr(updater.subprocess, name, None) for name in ["CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"]}

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        class Dummy:
            pass
        return Dummy()

    try:
        updater.subprocess.CREATE_NO_WINDOW = 0x08000000
        updater.subprocess.CREATE_NEW_PROCESS_GROUP = 0x00000200
        updater.subprocess.DETACHED_PROCESS = 0x00000008
        updater.subprocess.Popen = fake_popen
        updater._launch_helper_script(Path("apply_update.ps1"), Path("."))
    finally:
        updater.subprocess.Popen = old_popen
        for name, value in saved_flags.items():
            if value is None:
                try:
                    delattr(updater.subprocess, name)
                except AttributeError:
                    pass
            else:
                setattr(updater.subprocess, name, value)

    args, kwargs = calls[0]
    check("-WindowStyle" in args[0] and "Hidden" in args[0], "PowerShell helper starts hidden")
    flags = kwargs.get("creationflags", 0)
    check(flags & 0x08000000 and flags & 0x00000200 and flags & 0x00000008, "helper is detached from parent process")
    check(kwargs.get("stdin") is updater.subprocess.DEVNULL, "helper stdin is detached")
    check(kwargs.get("stdout") is updater.subprocess.DEVNULL, "helper stdout is detached")
    check(kwargs.get("stderr") is updater.subprocess.DEVNULL, "helper stderr is detached")
    env = kwargs.get("env") or {}
    check(env.get("PYINSTALLER_RESET_ENVIRONMENT") == "1", "helper launch receives clean PyInstaller reset env")
    check("_PYI_APPLICATION_HOME_DIR" not in env, "helper launch strips PyInstaller parent env")


def test_pyinstaller_child_environment_is_sanitized():
    print("[7] PyInstaller child environment sanitization")
    old_env = dict(os.environ)
    had_meipass = hasattr(sys, "_MEIPASS")
    old_meipass = getattr(sys, "_MEIPASS", None)
    runtime_dir = str(Path(tempfile.gettempdir()) / "_MEI12345")
    try:
        sys._MEIPASS = runtime_dir
        os.environ["_PYI_APPLICATION_HOME_DIR"] = runtime_dir
        os.environ["_MEIPASS2"] = runtime_dir
        os.environ["PATH"] = runtime_dir + os.pathsep + os.environ.get("PATH", "")
        env = process_env.sanitized_subprocess_env()
        check("_PYI_APPLICATION_HOME_DIR" not in env, "_PYI env vars are removed")
        check("_MEIPASS2" not in env, "legacy _MEIPASS2 env var is removed")
        check(runtime_dir not in env.get("PATH", ""), "runtime temp dir is removed from PATH")
        check(env.get("PYINSTALLER_RESET_ENVIRONMENT") == "1", "fresh PyInstaller launch flag is set")
    finally:
        os.environ.clear()
        os.environ.update(old_env)
        if had_meipass:
            sys._MEIPASS = old_meipass
        else:
            try:
                delattr(sys, "_MEIPASS")
            except AttributeError:
                pass



def test_exit_watchdog_uses_detached_powershell():
    print("[8] detached exit watchdog")
    calls = []
    old_name = updater.os.name
    old_popen = updater.subprocess.Popen
    saved_flags = {name: getattr(updater.subprocess, name, None) for name in ["CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"]}

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        class Dummy:
            pass
        return Dummy()

    try:
        updater.os.name = "nt"
        updater.subprocess.CREATE_NO_WINDOW = 0x08000000
        updater.subprocess.CREATE_NEW_PROCESS_GROUP = 0x00000200
        updater.subprocess.DETACHED_PROCESS = 0x00000008
        updater.subprocess.Popen = fake_popen
        ok = updater._spawn_exit_watchdog(12345, 3)
    finally:
        updater.os.name = old_name
        updater.subprocess.Popen = old_popen
        for name, value in saved_flags.items():
            if value is None:
                try:
                    delattr(updater.subprocess, name)
                except AttributeError:
                    pass
            else:
                setattr(updater.subprocess, name, value)

    check(ok is True, "watchdog is scheduled on Windows")
    args, kwargs = calls[0]
    command = args[0]
    check(command[0] == "powershell.exe", "watchdog uses PowerShell instead of cmd timeout")
    check("-WindowStyle" in command and "Hidden" in command, "watchdog PowerShell window is hidden")
    check("Stop-Process -Id 12345 -Force" in command[-1], "watchdog force-stops current process")
    check(kwargs.get("env", {}).get("PYINSTALLER_RESET_ENVIRONMENT") == "1", "watchdog receives clean PyInstaller env")

def main():
    print("=" * 56)
    print("updater tests")
    print("=" * 56)
    for fn in [test_version_compare, test_trusted_update_url, test_manifest_accepts_utf8_bom, test_non_frozen_install_blocked, test_helper_script_forces_old_process_and_restarts_from_app_dir, test_helper_process_is_hidden_and_detached, test_pyinstaller_child_environment_is_sanitized, test_exit_watchdog_uses_detached_powershell]:
        fn()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()