import sys
import os
import socket
import subprocess
import shutil
import glob

# --noconsole 模式下 stdout/stderr 为 None，uvicorn logging 会崩溃
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")


# ─── 启动前检测 ────────────────────────────────────────────────

def _check_port(port: int = 8080):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "启动失败",
                f"端口 {port} 已被占用，程序无法启动。\n\n"
                "请检查是否有另一个「自动下单系统Pro」正在运行，\n"
                "关闭后重新双击启动。"
            )
            root.destroy()
            sys.exit(1)


def _system_chrome_ok() -> bool:
    for c in [
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]:
        if c and os.path.isfile(c):
            return True
    return False


def _pw_chromium_ok() -> bool:
    pattern = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "ms-playwright", "chromium-*", "chrome-win", "chrome.exe",
    )
    return bool(glob.glob(pattern))


def _pw_driver() -> tuple:
    # Method 1: playwright 内部 API（开发模式 & PyInstaller 均适用）
    try:
        from playwright._impl._driver import compute_driver_executable
        node, cli = compute_driver_executable()
        if os.path.isfile(str(node)):
            return str(node), str(cli)
    except Exception:
        pass
    # Method 2: 直接构造路径（兜底）
    try:
        import playwright as _pw
        base = os.path.dirname(_pw.__file__)
        node = os.path.join(base, "driver", "node.exe")
        cli = os.path.join(base, "driver", "package", "cli.js")
        if os.path.isfile(node):
            return node, cli
    except Exception:
        pass
    return None, None


def _install_pw_chromium():
    node, cli = _pw_driver()

    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("初始化浏览器环境")
    root.geometry("480x160")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    # 居中显示
    root.update_idletasks()
    x = (root.winfo_screenwidth() - 480) // 2
    y = (root.winfo_screenheight() - 160) // 2
    root.geometry(f"480x160+{x}+{y}")

    tk.Label(
        root,
        text="首次运行：正在下载内置浏览器（约 200 MB）\n下载完成后程序将自动启动，请保持网络畅通...",
        font=("Microsoft YaHei", 10),
        pady=14,
    ).pack()
    bar = ttk.Progressbar(root, mode="indeterminate", length=440)
    bar.pack(pady=4)
    sv = tk.StringVar(value="准备中...")
    tk.Label(root, textvariable=sv, fg="#555", font=("Microsoft YaHei", 9)).pack()
    bar.start(10)
    root.update()

    ok = False
    if node and cli:
        try:
            proc = subprocess.Popen(
                [node, cli, "install", "chromium"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if line.strip():
                    sv.set(line.strip()[:80])
                    root.update()
            ok = proc.returncode == 0
        except Exception as e:
            sv.set(f"错误: {e}")
            root.update()
    else:
        sv.set("未找到安装程序（driver 未打包）")
        root.update()

    bar.stop()
    root.destroy()

    if not ok:
        root2 = tk.Tk()
        root2.withdraw()
        messagebox.showerror(
            "浏览器安装失败",
            "未能自动安装内置浏览器。\n\n"
            "请安装 Google Chrome 后重新启动本程序。\n\n"
            "下载地址：搜索「Google Chrome 下载」",
            parent=root2,
        )
        root2.destroy()


def _ensure_browser():
    """程序启动时确保有可用浏览器，没有则自动安装 Playwright Chromium。"""
    if _system_chrome_ok():
        return
    if _pw_chromium_ok():
        return
    _install_pw_chromium()

import uvicorn
import webbrowser
import threading
import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from api.routes import router as api_router
from api.ws import router as ws_router, _broadcast_loop
from core.db import init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(_broadcast_loop())
    yield


app = FastAPI(lifespan=lifespan, title="自动下单系统 Pro")
app.include_router(api_router, prefix="/api")
app.include_router(ws_router)

# 挂载 assets/ 子目录（JS/CSS）
_assets_dir = os.path.join(STATIC_DIR, "assets")
if os.path.exists(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")


# SPA fallback — 所有其他 GET 请求返回 index.html
@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str = ""):
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index, media_type="text/html")
    return {"error": "前端未构建，请先运行 build.bat"}


def _open_browser():
    import time
    time.sleep(1.8)
    webbrowser.open("http://localhost:8080")


if __name__ == "__main__":
    _check_port()
    _ensure_browser()
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
