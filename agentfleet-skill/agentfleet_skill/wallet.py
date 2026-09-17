"""
链上钱包管理
============
生成 ETH 地址、管理私钥、查询余额、发送交易。

Provider Agent 需要有自己的链上钱包来接收 ETH 付款。
"""

import os, json
from eth_account import Account
from eth_account.signers.local import LocalAccount


WALLET_DIR = os.path.expanduser("~/.agentfleet/wallets")


def ensure_wallet_dir():
    os.makedirs(WALLET_DIR, exist_ok=True)


class AgentWallet:
    """
    Agent 链上钱包。
    管理 ETH 地址和私钥，用于接收链上支付。
    """

    def __init__(self, name: str = "default"):
        ensure_wallet_dir()
        self.name = name
        self.keyfile = os.path.join(WALLET_DIR, f"{name}.json")
        self._account: LocalAccount | None = None

    @property
    def account(self) -> LocalAccount:
        if self._account is None:
            self._account = self._load_or_create()
        return self._account

    @property
    def address(self) -> str:
        return self.account.address

    def _load_or_create(self) -> LocalAccount:
        if os.path.exists(self.keyfile):
            with open(self.keyfile) as f:
                data = json.load(f)
            return Account.from_key(data["private_key"])
        acct = Account.create()
        with open(self.keyfile, "w") as f:
            json.dump({"private_key": acct.key.hex(), "address": acct.address}, f)
        os.chmod(self.keyfile, 0o600)
        return acct

    def export_private_key(self) -> str:
        """导出私钥 (⚠️ 谨慎使用)"""
        return self.account.key.hex()

    def get_address(self) -> str:
        return self.address

    @staticmethod
    def from_private_key(key_hex: str) -> "AgentWallet":
        """从已有私钥恢复钱包"""
        w = AgentWallet.__new__(AgentWallet)
        w.name = "imported"
        w.keyfile = ""
        w._account = Account.from_key(key_hex)
        return w
