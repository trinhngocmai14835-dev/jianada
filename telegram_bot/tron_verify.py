"""验证 TRC-20 USDT 链上转账"""
import time
from typing import Optional, Tuple

import aiohttp

from config import TRONSCAN_API_KEY

USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_DECIMALS = 6
TRON_SCAN_API = "https://apilist.tronscanapi.com/api/transaction-info"
TRON_ADDRESS_TRC20_API = "https://apilist.tronscanapi.com/api/transfer/trc20"


def _headers():
    headers = {"Accept": "application/json"}
    if TRONSCAN_API_KEY:
        headers["TRON-PRO-API-KEY"] = TRONSCAN_API_KEY
    return headers


def format_usdt_amount(amount: float) -> str:
    text = f"{float(amount):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _token_decimals(transfer: dict) -> int:
    token_info = transfer.get("tokenInfo") or {}
    decimals = transfer.get("decimals") or token_info.get("tokenDecimal") or USDT_DECIMALS
    try:
        return int(decimals)
    except (TypeError, ValueError):
        return USDT_DECIMALS


def _raw_to_amount(raw, decimals: int = USDT_DECIMALS) -> float:
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if not text:
        return 0.0
    try:
        if "." in text:
            return float(text)
        return int(text) / (10 ** decimals)
    except (TypeError, ValueError):
        return float(raw) / (10 ** decimals)


def _transfer_amount(transfer: dict) -> float:
    raw = transfer.get("amount_str")
    if raw is None:
        raw = transfer.get("quant")
    if raw is None:
        raw = transfer.get("amount")
    return _raw_to_amount(raw, _token_decimals(transfer))


def _contract_address(transfer: dict) -> str:
    token_info = transfer.get("tokenInfo") or {}
    return (
        transfer.get("contract_address")
        or transfer.get("contractAddress")
        or transfer.get("trc20Id")
        or token_info.get("tokenId")
        or ""
    )


def _from_address(transfer: dict) -> str:
    return transfer.get("from_address") or transfer.get("fromAddress") or ""


def _to_address(transfer: dict) -> str:
    return transfer.get("to_address") or transfer.get("toAddress") or ""


def _txhash(transfer: dict) -> str:
    return transfer.get("transaction_id") or transfer.get("hash") or transfer.get("txid") or ""


def _is_success_transfer(transfer: dict) -> bool:
    confirmed = transfer.get("confirmed")
    if confirmed in (False, 0, "0"):
        return False

    status = transfer.get("status")
    if status not in (None, 0, "0"):
        return False

    if transfer.get("revert") in (True, 1, "1"):
        return False

    contract_ret = (transfer.get("contractRet") or transfer.get("contract_ret") or "").upper()
    final_result = (transfer.get("finalResult") or transfer.get("final_result") or "").upper()
    if contract_ret and contract_ret != "SUCCESS":
        return False
    if final_result and final_result != "SUCCESS":
        return False

    event_type = (transfer.get("event_type") or transfer.get("type") or transfer.get("eventType") or "").lower()
    if event_type and event_type != "transfer":
        return False

    return True


def _matches_wallet(transfer: dict, wallet: str) -> bool:
    contract_ok = _contract_address(transfer).upper() == USDT_CONTRACT.upper()
    addr_ok = _to_address(transfer).upper() == wallet.upper()
    return contract_ok and addr_ok


def _extract_trc20_transfers(data: dict) -> list:
    transfers = list(data.get("trc20TransferInfo") or data.get("transfersAllList") or [])
    token_transfer = data.get("tokenTransferInfo")
    if token_transfer:
        transfers.append(token_transfer)
    return transfers


def _normalize_incoming_transfer(transfer: dict, wallet: str, txhash_override: str = ""):
    txhash = txhash_override or _txhash(transfer)
    if not txhash:
        return None
    if not _matches_wallet(transfer, wallet):
        return None
    if not _is_success_transfer(transfer):
        return None

    return {
        "txhash": txhash,
        "from_address": _from_address(transfer),
        "to_address": _to_address(transfer),
        "amount": _transfer_amount(transfer),
        "block_timestamp": transfer.get("block_ts") or transfer.get("block_timestamp") or transfer.get("timestamp"),
        "block": transfer.get("block"),
    }


async def _fetch_transaction_info(txhash: str) -> dict:
    url = f"{TRON_SCAN_API}?hash={txhash}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"查询失败 (HTTP {resp.status})")
            return await resp.json()


async def verify_usdt_deposit(txhash: str, wallet: str) -> Tuple[bool, str, Optional[dict]]:
    """确认 TxHash 是否为转入 wallet 的 TRC-20 USDT，金额不限。"""
    try:
        data = await _fetch_transaction_info(txhash)
    except Exception as e:
        return False, f"网络错误: {e}", None

    if data.get("confirmed") is False:
        return False, "交易未确认，请稍后重试", None

    for transfer in _extract_trc20_transfers(data):
        deposit = _normalize_incoming_transfer(transfer, wallet, txhash_override=txhash)
        if not deposit:
            continue
        if deposit["amount"] <= 0:
            return False, "到账金额必须大于 0 USDT", None
        amount = format_usdt_amount(deposit["amount"])
        return True, f"验证成功，到账 {amount} USDT", deposit

    return False, "未找到转入收款地址的 TRC-20 USDT 记录", None


async def verify_usdt_payment(
    txhash: str, wallet: str, expected_usdt: float, tolerance: float = 0.5
) -> Tuple[bool, str]:
    """
    Returns (success, message).
    查询 TronScan，确认 txhash 是向 wallet 转入 expected_usdt USDT（TRC-20）。
    """
    try:
        data = await _fetch_transaction_info(txhash)
    except Exception as e:
        return False, f"网络错误: {e}"

    if data.get("confirmed") is False:
        return False, "交易未确认，请稍后重试"

    amount_mismatch = None
    for transfer in _extract_trc20_transfers(data):
        if not _matches_wallet(transfer, wallet):
            continue
        if not _is_success_transfer(transfer):
            continue
        amount = _transfer_amount(transfer)
        if abs(amount - expected_usdt) <= tolerance:
            return True, f"验证成功，收款 {format_usdt_amount(amount)} USDT"
        amount_mismatch = amount

    if amount_mismatch is not None:
        return False, f"金额不符：收到 {format_usdt_amount(amount_mismatch)} USDT，期望 {expected_usdt} USDT"
    return False, "未找到匹配记录（目标地址或合约不匹配）"


async def fetch_usdt_incoming_transfers(wallet: str, limit: int = 20, lookback_minutes: int = 180) -> list:
    """拉取指定地址最近的已确认 TRC-20 USDT 入账。"""
    params = {
        "address": wallet,
        "trc20Id": USDT_CONTRACT,
        "direction": 2,
        "db_version": 0,
        "reverse": "true",
        "start": 0,
        "limit": max(1, min(int(limit or 20), 50)),
    }
    if lookback_minutes:
        params["start_timestamp"] = int((time.time() - int(lookback_minutes) * 60) * 1000)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                TRON_ADDRESS_TRC20_API,
                params=params,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"TronScan 查询失败 (HTTP {resp.status})")
                data = await resp.json()
    except Exception as e:
        raise RuntimeError(f"查询 TRC20 入账失败: {e}") from e

    raw_transfers = data.get("data") or data.get("Data") or data.get("token_transfers") or []
    transfers = []
    for item in raw_transfers:
        transfer = _normalize_incoming_transfer(item, wallet)
        if transfer and transfer["amount"] > 0:
            transfers.append(transfer)

    return sorted(transfers, key=lambda x: int(x.get("block_timestamp") or 0))