"""验证 TRC-20 USDT 链上转账"""
import aiohttp

USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_DECIMALS = 6
TRON_SCAN_API = "https://apilist.tronscanapi.com/api/transaction-info"


async def verify_usdt_payment(
    txhash: str, wallet: str, expected_usdt: float, tolerance: float = 0.5
) -> tuple[bool, str]:
    """
    Returns (success, message).
    查询 TronScan，确认 txhash 是向 wallet 转入 expected_usdt USDT（TRC-20）。
    """
    url = f"{TRON_SCAN_API}?hash={txhash}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return False, f"查询失败 (HTTP {resp.status})"
                data = await resp.json()
    except Exception as e:
        return False, f"网络错误: {e}"

    if not data.get("confirmed"):
        return False, "交易未确认，请稍后重试"

    transfers = data.get("trc20TransferInfo", [])
    for t in transfers:
        contract_ok = t.get("contract_address", "") == USDT_CONTRACT
        addr_ok = t.get("to_address", "").upper() == wallet.upper()
        if not (contract_ok and addr_ok):
            continue
        amount = float(t.get("amount", 0)) / (10 ** USDT_DECIMALS)
        if abs(amount - expected_usdt) <= tolerance:
            return True, f"验证成功，收款 {amount} USDT"
        else:
            return False, f"金额不符：收到 {amount} USDT，期望 {expected_usdt} USDT"

    return False, "未找到匹配记录（目标地址或合约不匹配）"
