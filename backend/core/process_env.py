"""Helpers for launching external processes from PyInstaller builds."""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterator, Mapping, Optional

_PYINSTALLER_ENV_NAMES = {
    "_MEIPASS2",
    "PYINSTALLER_STRICT_UNPACK_MODE",
    "PYINSTALLER_RESET_ENVIRONMENT",
}
_PYINSTALLER_ENV_PREFIXES = ("_PYI_",)


def _norm_path(value: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(value))
    except Exception:
        return os.path.normcase(str(value or ""))


def _is_same_or_child(path: str, root: str) -> bool:
    try:
        path_obj = Path(path).resolve()
        root_obj = Path(root).resolve()
        return path_obj == root_obj or root_obj in path_obj.parents
    except Exception:
        norm_path = _norm_path(path)
        norm_root = _norm_path(root)
        return norm_path == norm_root or norm_path.startswith(norm_root + os.sep)


def _runtime_temp_dir() -> str:
    value = str(getattr(sys, "_MEIPASS", "") or os.environ.get("_PYI_APPLICATION_HOME_DIR", "") or "")
    return _norm_path(value) if value else ""


def sanitized_subprocess_env(extra: Optional[Mapping[str, str]] = None) -> dict:
    """Return an environment safe to pass to non-PyInstaller child processes.

    PyInstaller one-file apps expose their extraction directory through DLL search
    state and several environment variables. Letting browser/helper processes
    inherit those values can keep the _MEI directory alive after the app exits.
    """
    env = dict(os.environ)
    for name in list(env):
        if name in _PYINSTALLER_ENV_NAMES or name.startswith(_PYINSTALLER_ENV_PREFIXES):
            env.pop(name, None)

    runtime_dir = _runtime_temp_dir()
    if runtime_dir:
        for name, value in list(env.items()):
            if not value:
                continue
            if name.upper() == "PATH":
                parts = [part for part in value.split(os.pathsep) if part and not _is_same_or_child(part, runtime_dir)]
                env[name] = os.pathsep.join(parts)
            elif _is_same_or_child(value, runtime_dir):
                env.pop(name, None)

    # If the helper later starts a fresh PyInstaller app, make it a clean top-level
    # launch instead of inheriting one-file parent context.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def _get_windows_dll_directory() -> Optional[str]:
    if os.name != "nt":
        return None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        size = kernel32.GetDllDirectoryW(0, None)
        if size <= 0:
            return None
        buf = ctypes.create_unicode_buffer(size + 1)
        if kernel32.GetDllDirectoryW(len(buf), buf) <= 0:
            return None
        return buf.value or None
    except Exception:
        return None


def _set_windows_dll_directory(path: Optional[str]) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.SetDllDirectoryW(path))
    except Exception:
        return False


@contextlib.contextmanager
def clean_subprocess_context() -> Iterator[None]:
    """Temporarily reset the Windows DLL search directory for child launches."""
    previous = _get_windows_dll_directory()
    changed = _set_windows_dll_directory(None)
    try:
        yield
    finally:
        if changed:
            _set_windows_dll_directory(previous)


def leave_pyinstaller_temp_cwd(fallback: Optional[str] = None) -> bool:
    """Move cwd out of sys._MEIPASS if a frozen app was started from there."""
    runtime_dir = _runtime_temp_dir()
    if not runtime_dir:
        return False
    try:
        if not _is_same_or_child(os.getcwd(), runtime_dir):
            return False
        target = fallback or os.path.dirname(sys.executable) or tempfile.gettempdir()
        os.chdir(target)
        return True
    except Exception:
        return False