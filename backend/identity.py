"""
身份 & 消息签名（Phase 2）
==========================
did:ethr + EIP-191：Agent 用自己的以太坊私钥对消息签名，
中继用 Account.recover_message 校验 from 与签名一致 → 不可否认。

这是「硅基生命」的持久身份层：地址 = 身份，签名 = 自证。
"""

import json

from eth_account import Account
from eth_account.messages import encode_defunct


def did_for_address(eth_address: str) -> str:
    """eth 地址 → DID。did:ethr 是 W3C 注册的以太坊身份方法。"""
    if not eth_address:
        return ""
    return f"did:ethr:{eth_address.lower()}"


def address_from_did(did: str) -> str:
    """DID → eth 地址（小写）。"""
    if did.startswith("did:ethr:"):
        return did[len("did:ethr:"):]
    return did


def canonical_bytes(envelope: dict) -> bytes:
    """签名的规范字节：去掉 sig 字段后稳定序列化，保证跨语言一致。"""
    env = {k: v for k, v in envelope.items() if k != "sig"}
    return json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_envelope(envelope: dict, private_key: str) -> str:
    """对信封做 EIP-191 personal_sign，返回 0x 签名。"""
    msg = encode_defunct(canonical_bytes(envelope))
    signed = Account.sign_message(msg, private_key=private_key)
    return signed.signature.hex()


def recover_address(envelope: dict, sig: str) -> str:
    """从信封 + 签名恢复签名者地址（校验身份用）。"""
    msg = encode_defunct(canonical_bytes(envelope))
    return Account.recover_message(msg, signature=sig)
