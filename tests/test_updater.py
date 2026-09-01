"""Updater safety checks."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import updater


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


def test_non_frozen_install_blocked():
    print("[3] non-frozen install blocked")
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
    print("[4] helper script process takeover")
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
        check("Start-Process -FilePath $dst -WorkingDirectory $dir" in body, "new app restarts from its install directory")


def test_helper_process_is_hidden_and_detached():
    print("[5] detached helper launcher")
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

def main():
    print("=" * 56)
    print("updater tests")
    print("=" * 56)
    for fn in [test_version_compare, test_trusted_update_url, test_non_frozen_install_blocked, test_helper_script_forces_old_process_and_restarts_from_app_dir, test_helper_process_is_hidden_and_detached]:
        fn()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()