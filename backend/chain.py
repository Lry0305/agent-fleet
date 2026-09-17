"""
链上集成层 — 把 credits 交易映射到链上 Escrow 合约
==================================================
双轨制:
  - SQLite credits: 快速对账、前端展示
  - 链上 ETH Escrow: 去信任化的实际结算

链上操作是可选的 (dev 模式开关), 但存在时提供去中心化保证。
"""

import os
from web3 import Web3
from eth_account import Account
from backend.config import RPC_URL, CHAIN_ID, ESCROW_PAYMENT, SERVICE_REGISTRY, REPUTATION

# 合约 ABI (从 artifacts 读取)
import json, sys
from pathlib import Path

_ARTIFACTS = Path(__file__).parent.parent / "artifacts"


def _load_abi(name: str) -> list:
    p = _ARTIFACTS / "contracts" / f"{name}.sol" / f"{name}.json"
    if p.exists():
        return json.loads(p.read_text())["abi"]
    # fallback: minimal ABI
    return []


ESCROW_ABI = _load_abi("EscrowPayment")
REGISTRY_ABI = _load_abi("ServiceRegistry")
REPUTATION_ABI = _load_abi("Reputation")

# ── 懒加载 Web3 ──
_w3: Web3 | None = None
_escrow = None
_registry = None


def _get_w3() -> Web3 | None:
    global _w3
    if _w3 is None:
        try:
            _w3 = Web3(Web3.HTTPProvider(RPC_URL))
            if not _w3.is_connected():
                print("[Chain] ⚠️ 无法连接链，使用纯 off-chain 模式")
                _w3 = None
                return None
            print(f"[Chain] ✅ 已连接 chainId={_w3.eth.chain_id}")
        except Exception as e:
            print(f"[Chain] ⚠️ 链连接失败: {e}")
            _w3 = None
    return _w3


def chain_available() -> bool:
    return _get_w3() is not None and bool(ESCROW_PAYMENT)


def lock_funds_on_chain(
    consumer_private_key: str,
    provider_address: str,
    amount_credits: float,
) -> dict | None:
    """
    将 credits 对应的 ETH 锁入 EscrowPayment 合约。
    返回 {tx_hash, request_id} 或 None（链不可用时）。
    """
    w3 = _get_w3()
    if not w3 or not ESCROW_PAYMENT or not consumer_private_key:
        return None

    try:
        from backend.config import CREDIT_TO_ETH_RATE
        amount_eth = amount_credits * CREDIT_TO_ETH_RATE
        escrow = w3.eth.contract(
            address=Web3.to_checksum_address(ESCROW_PAYMENT),
            abi=ESCROW_ABI,
        )
        acct = Account.from_key(consumer_private_key)
        fn = escrow.functions.requestService(
            Web3.to_checksum_address(provider_address), 0  # service_id=0 if not on-chain
        )
        nonce = w3.eth.get_transaction_count(acct.address)
        tx = fn.build_transaction({
            "from": acct.address, "nonce": nonce,
            "value": w3.to_wei(amount_eth, "ether"),
            "gas": 500000, "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, consumer_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        # 解析 requestId（事件解析失败时用 getRequestCount()-1 兜底）
        request_id = None
        try:
            logs = escrow.events.ServiceRequested().process_receipt(receipt)
            if logs:
                request_id = logs[0]["args"]["requestId"]
        except Exception:
            pass
        if request_id is None:
            try:
                request_id = escrow.functions.getRequestCount().call() - 1
            except Exception:
                request_id = 0

        return {
            "tx_hash": tx_hash.hex(),
            "request_id": request_id,
            "amount_eth": amount_eth,
        }
    except Exception as e:
        print(f"[Chain] 锁定资金失败: {e}")
        return None


def release_funds_on_chain(
    consumer_private_key: str,
    chain_request_id: int,
) -> dict | None:
    """
    Consumer 确认 → 链上释放 ETH 给 Provider。
    """
    w3 = _get_w3()
    if not w3 or not ESCROW_PAYMENT or not consumer_private_key:
        return None

    try:
        escrow = w3.eth.contract(
            address=Web3.to_checksum_address(ESCROW_PAYMENT),
            abi=ESCROW_ABI,
        )
        acct = Account.from_key(consumer_private_key)
        fn = escrow.functions.confirmDelivery(chain_request_id)
        nonce = w3.eth.get_transaction_count(acct.address)
        tx = fn.build_transaction({
            "from": acct.address, "nonce": nonce,
            "gas": 300000, "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, consumer_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        return {"tx_hash": tx_hash.hex()}
    except Exception as e:
        print(f"[Chain] 释放资金失败: {e}")
        return None


def deliver_result_on_chain(
    provider_private_key: str,
    chain_request_id: int,
    data_hash_hex: str,
) -> dict | None:
    """
    Provider 交付结果 → 提交内容哈希上链存证。
    confirmDelivery 要求 state == Delivered，所以必须先调 deliverResult。
    data_hash_hex = 0x...（32 字节，通常 = keccak256(交付物内容)，即 artifact cid）。
    """
    w3 = _get_w3()
    if not w3 or not ESCROW_PAYMENT or not provider_private_key:
        return None

    try:
        escrow = w3.eth.contract(
            address=Web3.to_checksum_address(ESCROW_PAYMENT),
            abi=ESCROW_ABI,
        )
        acct = Account.from_key(provider_private_key)
        hx = data_hash_hex if data_hash_hex.startswith("0x") else "0x" + data_hash_hex
        data_hash = Web3.to_bytes(hexstr=hx)
        fn = escrow.functions.deliverResult(chain_request_id, data_hash)
        nonce = w3.eth.get_transaction_count(acct.address)
        tx = fn.build_transaction({
            "from": acct.address, "nonce": nonce,
            "gas": 300000, "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, provider_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        return {"tx_hash": tx_hash.hex()}
    except Exception as e:
        print(f"[Chain] 交付结果上链失败: {e}")
        return None


def get_onchain_balance(address: str) -> float:
    """查询链上 ETH 余额"""
    w3 = _get_w3()
    if not w3:
        return 0.0
    try:
        wei = w3.eth.get_balance(Web3.to_checksum_address(address))
        return float(w3.from_wei(wei, "ether"))
    except:
        return 0.0


# ═══════════════════════════════════════════════════════════
#  ServiceRegistry — 链上服务注册
# ═══════════════════════════════════════════════════════════

def _get_registry():
    w3 = _get_w3()
    if not w3 or not SERVICE_REGISTRY:
        return None, None
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(SERVICE_REGISTRY),
        abi=REGISTRY_ABI,
    )
    return w3, contract


def register_service_onchain(
    provider_private_key: str,
    name: str,
    description: str,
    price_eth: float,
) -> dict | None:
    """
    链上注册服务到 ServiceRegistry 合约。
    返回 {tx_hash, service_id} 或 None。
    """
    w3, registry = _get_registry()
    if not w3 or not registry or not provider_private_key:
        return None
    try:
        acct = Account.from_key(provider_private_key)
        price_wei = w3.to_wei(price_eth, "ether")
        fn = registry.functions.registerService(name, description, price_wei)
        nonce = w3.eth.get_transaction_count(acct.address)
        tx = fn.build_transaction({
            "from": acct.address, "nonce": nonce,
            "gas": 500000, "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, provider_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        # 解析 serviceId (从 ServiceRegistered 事件)
        service_id = None
        try:
            logs = registry.events.ServiceRegistered().process_receipt(receipt)
            if logs:
                service_id = logs[0]["args"]["serviceId"]
        except:
            # fallback: 服务总数 - 1
            service_id = registry.functions.getServiceCount().call() - 1

        return {"tx_hash": receipt.transactionHash.hex(), "service_id": service_id}
    except Exception as e:
        print(f"[Chain] 注册服务失败: {e}")
        return None


def get_services_onchain_by_name(name: str) -> list:
    """链上按名字查询活跃服务（getService 直接返回 Service[] 结构）"""
    w3, registry = _get_registry()
    if not w3 or not registry:
        return []
    try:
        services = registry.functions.getService(name).call()
        result = []
        for svc in services:
            result.append({
                "id": svc[0],
                "provider": svc[1],
                "name": svc[2],
                "description": svc[3],
                "price_wei": svc[4],
                "is_active": svc[5],
            })
        return result
    except Exception as e:
        print(f"[Chain] 查询服务失败: {e}")
        return []


# ═══════════════════════════════════════════════════════════
#  Reputation — 链上声誉评分
# ═══════════════════════════════════════════════════════════

def _get_reputation_contract():
    w3 = _get_w3()
    if not w3 or not REPUTATION:
        return None, None
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(REPUTATION),
        abi=REPUTATION_ABI,
    )
    return w3, contract


def rate_provider_onchain(
    consumer_private_key: str,
    provider_address: str,
    score: int,
) -> str | None:
    """
    Consumer 在链上给 Provider 评分 (1-5)。
    返回 tx_hash 或 None。
    """
    w3, rep = _get_reputation_contract()
    if not w3 or not rep or not consumer_private_key:
        return None
    try:
        acct = Account.from_key(consumer_private_key)
        fn = rep.functions.rateProvider(
            Web3.to_checksum_address(provider_address), score
        )
        nonce = w3.eth.get_transaction_count(acct.address)
        tx = fn.build_transaction({
            "from": acct.address, "nonce": nonce,
            "gas": 200000, "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, consumer_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        return receipt.transactionHash.hex()
    except Exception as e:
        print(f"[Chain] 链上评分失败: {e}")
        return None


def get_onchain_reputation(provider_address: str) -> dict:
    """查询链上 Provider 声誉"""
    w3, rep = _get_reputation_contract()
    if not w3 or not rep:
        return {"average": 0, "total": 0, "completed": 0}
    try:
        data = rep.functions.reputations(
            Web3.to_checksum_address(provider_address)
        ).call()
        total, sum_scores, completed = data[0], data[1], data[2]
        avg = sum_scores / total if total > 0 else 0
        return {
            "average": round(avg, 2),
            "total_ratings": total,
            "completed_jobs": completed,
        }
    except Exception as e:
        print(f"[Chain] 查询声誉失败: {e}")
        return {"average": 0, "total": 0, "completed": 0}
