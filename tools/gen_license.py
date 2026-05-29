"""
授权码生成工具 — 仅供管理员使用（需要 private_key.pem）
用法: python gen_license.py <机器码> <有效天数>
示例: python gen_license.py ABCD1234EFGH5678 30
"""

import sys
import os
import base64
from datetime import datetime, timedelta

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(_TOOLS_DIR) == "__pycache__":
    _TOOLS_DIR = os.path.dirname(_TOOLS_DIR)
_KEY_PATH = os.path.join(_TOOLS_DIR, "private_key.pem")

# 复用 backend 的 get_machine_id
sys.path.insert(0, os.path.join(_TOOLS_DIR, '..', 'backend'))
from core.license import get_machine_id


def _load_private_key():
    if not os.path.isfile(_KEY_PATH):
        print(f"❌ 未找到私钥文件: {_KEY_PATH}")
        print("   请确保 private_key.pem 在 tools/ 目录下，不要发给客户！")
        sys.exit(1)
    from cryptography.hazmat.primitives import serialization
    with open(_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def generate_license(machine_id: str, days: int, base_date: datetime = None) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = _load_private_key()
    start = base_date if (base_date and base_date > datetime.now()) else datetime.now()
    expiry = (start + timedelta(days=days)).strftime("%Y%m%d")
    data = f"{machine_id.upper()}|{expiry}".encode()
    sig = private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(sig).decode().rstrip("=")
    return f"{expiry}.{sig_b64}"


def main():
    if len(sys.argv) == 3:
        machine_id = sys.argv[1]
        days = int(sys.argv[2])
    elif len(sys.argv) == 2:
        machine_id = sys.argv[1]
        days = int(input("有效天数 (例如 30): ").strip())
    else:
        print("=" * 50)
        print("  授权码生成工具（RSA签名版）")
        print("=" * 50)
        print(f"\n本机机器码: {get_machine_id()}")
        print("\n[1] 为指定机器码生成授权码")
        print("[2] 为本机生成授权码（测试用）")
        choice = input("\n选择 (1/2): ").strip()
        if choice == "2":
            machine_id = get_machine_id()
        else:
            machine_id = input("输入机器码: ").strip().upper()
        days = int(input("有效天数 (例如 30): ").strip())

    key = generate_license(machine_id.upper(), days)
    expiry = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")
    print(f"\n✅ 生成成功！")
    print(f"   机器码:   {machine_id.upper()}")
    print(f"   到期日:   {expiry}（{days}天后）")
    print(f"\n   授权码（发给客户完整复制）:")
    print(f"   {key}")
    print()


if __name__ == "__main__":
    main()
