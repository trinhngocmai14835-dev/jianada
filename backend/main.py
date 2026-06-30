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

def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_our_app(port: int) -> bool:
    """占用该端口的是否就是本程序的另一个实例（通过页面标题识别）。"""
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
            body = r.read(8192).decode("utf-8", "ignore")
        return "自动下单系统" in body
    except Exception:
        return False


def _msgbox(title: str, text: str, icon: int = 0x40):
    """Windows 原生消息框，不依赖 tkinter（frozen EXE 里 tkinter 可能未打包）。
    icon: 0x40=信息(i) / 0x10=错误(x)。"""
    try:
        import ctypes
        MB_OK = 0x0
        MB_TOPMOST = 0x00040000
        MB_SETFOREGROUND = 0x00010000
        ctypes.windll.user32.MessageBoxW(
            0, text, title, MB_OK | icon | MB_TOPMOST | MB_SETFOREGROUND
        )
    except Exception:
        pass


def _check_port(port: int = 8080):
    if not _port_in_use(port):
        return  # 端口空闲，正常启动

    import webbrowser
    if _is_our_app(port):
        # 占用方就是本程序的另一个实例 —— 直接复用，打开界面而不是报错退出
        webbrowser.open(f"http://localhost:{port}")
        _msgbox(
            "程序已在运行",
            "「自动下单系统Pro」已经在运行，已为你打开它的界面。\n\n"
            "无需重复启动。\n\n"
            "如果想重新启动：先在任务管理器结束所有「自动下单系统Pro」进程，再双击本程序。",
            0x40,
        )
        sys.exit(0)

    # 端口被其它程序占用
    _msgbox(
        "启动失败",
        f"端口 {port} 被【其它程序】占用，本程序无法启动。\n\n"
        "请关闭占用该端口的程序后重试；\n"
        "若不确定是哪个程序，重启电脑后再双击启动。",
        0x10,
    )
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


# index.html 禁止缓存：换版本后浏览器立即加载新页面（新 index 引用新哈希的 JS），
# 避免客户因缓存看到旧界面。带哈希的 assets/*.js 不受影响（可缓存，本就不可变）。
_NO_CACHE = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


# SPA fallback — 所有其他 GET 请求返回 index.html
@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str = ""):
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index, media_type="text/html", headers=_NO_CACHE)
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
