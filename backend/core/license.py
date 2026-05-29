import hashlib
import uuid
import platform
import base64
from datetime import datetime

# ── 公钥（验证用，打包进EXE；私钥只留在管理员 tools/ 目录）──────────
_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAl7KBnbsO85BrOv9pVmRd
WyeuxxRu4yLS4Hpf0yly2Cna8fNmHBJxvul+rzxjKIjXafLuPe3HQPhKfGQumqkJ
b+bBGobqviaPd7ctwC5hkm3mZjJMICvjV//tiIdvIkMs5Ibv2OTsE0wNvoG3ToSB
VCn6BOM9t4XncjPdIPrrwXCi43Qhgzlmkz4Le7gZ6R/WYg8hOicWZk0PqrYTku1W
QbN/CNgDY7r5mzJea53qqJVfBuwyvGpvjGi7/Yyi8GIEk6eZk6Oba7/fvF/EIDy3
y70ayJ5HRJGCQGx/sU+k+Q+cIiEegNr+i49RVwvBTlfD0/ReyDwfblZCyiJn3lAB
nwIDAQAB
-----END PUBLIC KEY-----"""


def get_machine_id() -> str:
    mac = hex(uuid.getnode())[2:].upper()
    hostname = platform.node()
    raw = f"{mac}:{hostname}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()


def validate_license(key: str):
    """Returns (valid: bool, message: str, expiry: str)"""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        key = key.strip().replace(" ", "").replace("\n", "")
        if "." not in key:
            return False, "授权码格式无效", ""

        dot = key.index(".")
        expiry_str = key[:dot]
        sig_b64 = key[dot + 1:]

        if len(expiry_str) != 8:
            return False, "授权码格式无效", ""

        # 补 base64 padding
        pad = (4 - len(sig_b64) % 4) % 4
        sig = base64.b64decode(sig_b64 + "=" * pad)

        mid = get_machine_id()
        data = f"{mid}|{expiry_str}".encode()

        pub_key = serialization.load_pem_public_key(_PUBLIC_KEY_PEM)
        # 验证签名（签名不对会抛异常）
        pub_key.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())

        expiry_date = datetime.strptime(expiry_str, "%Y%m%d")
        if expiry_date < datetime.now():
            return False, f"授权码已于 {expiry_str} 过期", expiry_str

        remaining = (expiry_date - datetime.now()).days
        return True, f"授权有效，剩余 {remaining} 天（到期 {expiry_str}）", expiry_str

    except Exception as e:
        msg = str(e)
        if "Invalid signature" in msg or "Signature verification failed" in msg or "verification" in msg.lower():
            return False, "授权码与本机不匹配", ""
        return False, f"授权码无效: {e}", ""
