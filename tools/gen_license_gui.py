"""
授权码生成工具 GUI — 双击运行
依赖：同目录的 private_key.pem（不能发给客户）
"""

import hashlib
import uuid
import platform
import base64
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(_TOOLS_DIR) == "__pycache__":
    _TOOLS_DIR = os.path.dirname(_TOOLS_DIR)
_KEY_PATH = os.path.join(_TOOLS_DIR, "private_key.pem")


def get_machine_id() -> str:
    mac = hex(uuid.getnode())[2:].upper()
    hostname = platform.node()
    raw = f"{mac}:{hostname}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()


def generate_license(machine_id: str, days: int) -> str:
    if not os.path.isfile(_KEY_PATH):
        raise FileNotFoundError(f"找不到私钥文件：{_KEY_PATH}\n请确保 private_key.pem 在 tools/ 目录下")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    with open(_KEY_PATH, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    expiry = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")
    data = f"{machine_id.upper()}|{expiry}".encode()
    sig = private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(sig).decode().rstrip("=")
    return f"{expiry}.{sig_b64}"


# ── GUI ───────────────────────────────────────────────────────

def copy_to_clipboard(root, text):
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()


def show_tip(label, text, ms=1500):
    label.config(text=text)
    label.after(ms, lambda: label.config(text=""))


def build_ui():
    root = tk.Tk()
    root.title("授权码生成工具")
    root.resizable(False, False)
    root.configure(bg="#f0f2f5")

    frame = tk.Frame(root, bg="#f0f2f5", padx=32, pady=28)
    frame.pack()

    tk.Label(frame, text="自动下单系统 Pro", font=("微软雅黑", 16, "bold"),
             bg="#f0f2f5", fg="#1a1a2e").grid(row=0, column=0, columnspan=3, pady=(0, 4))
    tk.Label(frame, text="授权码生成工具（RSA签名版）", font=("微软雅黑", 10),
             bg="#f0f2f5", fg="#888").grid(row=1, column=0, columnspan=3, pady=(0, 20))

    # 本机机器码
    tk.Label(frame, text="本机机器码：", font=("微软雅黑", 10),
             bg="#f0f2f5", anchor="w").grid(row=2, column=0, sticky="w", pady=(0, 4))
    own_mid = get_machine_id()
    own_var = tk.StringVar(value=own_mid)
    tk.Entry(frame, textvariable=own_var, width=22, state="readonly",
             font=("Consolas", 11), fg="#555", relief="solid", bd=1
             ).grid(row=2, column=1, sticky="ew", padx=(8, 6))
    tip_own = tk.Label(frame, text="", font=("微软雅黑", 8), bg="#f0f2f5", fg="#52c41a")
    tip_own.grid(row=3, column=1, sticky="w", padx=8)
    tk.Button(frame, text="复制", font=("微软雅黑", 9), width=5,
              relief="solid", bd=1, bg="#fff", activebackground="#e6f7ff",
              command=lambda: (copy_to_clipboard(root, own_mid), show_tip(tip_own, "已复制"))
              ).grid(row=2, column=2)

    ttk.Separator(frame, orient="horizontal").grid(
        row=4, column=0, columnspan=3, sticky="ew", pady=16)

    # 客户机器码
    tk.Label(frame, text="客户机器码：", font=("微软雅黑", 10),
             bg="#f0f2f5", anchor="w").grid(row=5, column=0, sticky="w", pady=(0, 4))
    mid_var = tk.StringVar()
    mid_entry = tk.Entry(frame, textvariable=mid_var, width=22,
                         font=("Consolas", 11), relief="solid", bd=1)
    mid_entry.grid(row=5, column=1, sticky="ew", padx=(8, 6))
    tk.Button(frame, text="贴入", font=("微软雅黑", 9), width=5,
              relief="solid", bd=1, bg="#fff", activebackground="#e6f7ff",
              command=lambda: mid_var.set(root.clipboard_get().strip().upper())
              ).grid(row=5, column=2)

    # 有效天数
    days_var = tk.IntVar(value=30)
    tk.Label(frame, text="有效天数：", font=("微软雅黑", 10),
             bg="#f0f2f5", anchor="w").grid(row=6, column=0, sticky="w", pady=(12, 4))

    days_row = tk.Frame(frame, bg="#f0f2f5")
    days_row.grid(row=6, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(12, 4))
    for label, d in [("7天", 7), ("30天", 30), ("90天", 90), ("365天", 365)]:
        tk.Button(days_row, text=label, font=("微软雅黑", 9), width=6,
                  relief="solid", bd=1, bg="#fff", activebackground="#e6f7ff",
                  command=lambda v=d: days_var.set(v)).pack(side="left", padx=(0, 6))

    tk.Spinbox(frame, from_=1, to=3650, textvariable=days_var,
               width=8, font=("微软雅黑", 10), relief="solid", bd=1
               ).grid(row=7, column=1, sticky="w", padx=(8, 0), pady=(0, 4))
    tk.Label(frame, text="天", font=("微软雅黑", 10), bg="#f0f2f5"
             ).grid(row=7, column=2, sticky="w")

    ttk.Separator(frame, orient="horizontal").grid(
        row=8, column=0, columnspan=3, sticky="ew", pady=16)

    # 生成
    result_var = tk.StringVar(value="——")
    info_var = tk.StringVar(value="")

    def on_generate():
        mid = mid_var.get().strip().upper()
        if not mid:
            if not messagebox.askyesno("提示", "客户机器码为空，是否为本机生成授权码？"):
                return
            mid = own_mid
        if len(mid) != 16 or not mid.isalnum():
            messagebox.showerror("错误", "机器码格式不对，应为16位字母数字")
            return
        try:
            days = int(days_var.get())
        except Exception:
            messagebox.showerror("错误", "天数必须是整数")
            return
        try:
            key = generate_license(mid, days)
            result_var.set(key)
            expiry = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")
            info_var.set(f"机器码 {mid}  ·  有效 {days} 天  ·  到期 {expiry}")
        except Exception as e:
            messagebox.showerror("生成失败", str(e))

    tk.Button(frame, text="  生 成 授 权 码  ", font=("微软雅黑", 12, "bold"),
              bg="#1677ff", fg="white", activebackground="#0958d9", activeforeground="white",
              relief="flat", padx=12, pady=8, cursor="hand2",
              command=on_generate).grid(row=9, column=0, columnspan=3, pady=(0, 16))

    # 结果（授权码较长，用多行文本框显示）
    tk.Label(frame, text="授权码：", font=("微软雅黑", 10),
             bg="#f0f2f5", anchor="w").grid(row=10, column=0, sticky="nw", pady=(0, 4))

    result_text = tk.Text(frame, width=34, height=5, font=("Consolas", 9),
                          fg="#1677ff", relief="solid", bd=1, wrap="char",
                          state="disabled", bg="#fff")
    result_text.grid(row=10, column=1, sticky="ew", padx=(8, 6))

    tip_result = tk.Label(frame, text="", font=("微软雅黑", 8), bg="#f0f2f5", fg="#52c41a")
    tip_result.grid(row=11, column=1, sticky="w", padx=8)

    def update_result(*_):
        val = result_var.get()
        result_text.config(state="normal")
        result_text.delete("1.0", "end")
        if val != "——":
            result_text.insert("1.0", val)
        result_text.config(state="disabled")

    result_var.trace_add("write", update_result)

    def copy_result():
        val = result_var.get()
        if val != "——":
            copy_to_clipboard(root, val)
            show_tip(tip_result, "已复制！发给客户粘贴即可")

    tk.Button(frame, text="复制", font=("微软雅黑", 9), width=5,
              relief="solid", bd=1, bg="#fff", activebackground="#e6f7ff",
              command=copy_result).grid(row=10, column=2, sticky="n")

    tk.Label(frame, textvariable=info_var, font=("微软雅黑", 8),
             bg="#f0f2f5", fg="#888").grid(row=12, column=0, columnspan=3, pady=(4, 0))

    root.bind("<Return>", lambda e: on_generate())
    mid_entry.focus()
    return root


if __name__ == "__main__":
    root = build_ui()
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")
    root.mainloop()
