"""
Web3.py 集成层
============
封装所有智能合约调用，为 LangGraph Agent 提供链上操作接口。
"""

from web3 import Web3
from eth_account import Account

from agents.abis import (
    EscrowPayment_ABI,
    ServiceRegistry_ABI,
    Reputation_ABI,
)
from agents.config import (
    RPC_URL,
    SERVICE_REGISTRY_ADDRESS,
    ESCROW_PAYMENT_ADDRESS,
    REPUTATION_ADDRESS,
)


class Web3Client:
    """统一的 Web3 客户端，管理合约实例和账户"""

    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        if not self.w3.is_connected():
            raise ConnectionError(f"无法连接到 RPC: {RPC_URL}")
        print(f"✅ 已连接到链 (chainId={self.w3.eth.chain_id})")

        # 合约实例（懒加载，需要等部署地址后使用）
        self._service_registry = None
        self._escrow_payment = None
        self._reputation = None

    # ── 合约懒加载 ──

    @property
    def service_registry(self):
        if self._service_registry is None:
            if not SERVICE_REGISTRY_ADDRESS:
                raise ValueError("SERVICE_REGISTRY_ADDRESS 未设置")
            self._service_registry = self.w3.eth.contract(
                address=Web3.to_checksum_address(SERVICE_REGISTRY_ADDRESS),
                abi=ServiceRegistry_ABI,
            )
        return self._service_registry

    @property
    def escrow_payment(self):
        if self._escrow_payment is None:
            if not ESCROW_PAYMENT_ADDRESS:
                raise ValueError("ESCROW_PAYMENT_ADDRESS 未设置")
            self._escrow_payment = self.w3.eth.contract(
                address=Web3.to_checksum_address(ESCROW_PAYMENT_ADDRESS),
                abi=EscrowPayment_ABI,
            )
        return self._escrow_payment

    @property
    def reputation(self):
        if self._reputation is None:
            if not REPUTATION_ADDRESS:
                raise ValueError("REPUTATION_ADDRESS 未设置")
            self._reputation = self.w3.eth.contract(
                address=Web3.to_checksum_address(REPUTATION_ADDRESS),
                abi=Reputation_ABI,
            )
        return self._reputation

    # ── 账户管理 ──

    def get_account(self, private_key: str):
        """从私钥创建账户"""
        acct = Account.from_key(private_key)
        return {
            "address": acct.address,
            "private_key": private_key,
        }

    def get_balance(self, address: str) -> float:
        """查询 ETH 余额（单位 ETH）"""
        wei = self.w3.eth.get_balance(Web3.to_checksum_address(address))
        return float(self.w3.from_wei(wei, "ether"))

    # ── 交易发送 ──

    def send_transaction(self, contract_fn, from_account: dict, value_eth: float = 0):
        """
        发送链上交易并等待回执。
        contract_fn: 已 build 的合约函数调用对象
        from_account: {"address": ..., "private_key": ...}
        """
        nonce = self.w3.eth.get_transaction_count(from_account["address"])
        value_wei = self.w3.to_wei(value_eth, "ether")

        tx = contract_fn.build_transaction({
            "from": from_account["address"],
            "nonce": nonce,
            "value": value_wei,
            "gas": 500000,  # Gas limit
            "gasPrice": self.w3.eth.gas_price,
        })

        signed = self.w3.eth.account.sign_transaction(tx, from_account["private_key"])
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError(f"交易失败: {tx_hash.hex()}")
        return receipt, tx_hash

    # ── ServiceRegistry 操作 ──

    def register_service(
        self, name: str, description: str, price_eth: float, from_account: dict
    ):
        """供应商注册服务"""
        price_wei = self.w3.to_wei(price_eth, "ether")
        fn = self.service_registry.functions.registerService(name, description, price_wei)
        receipt, tx_hash = self.send_transaction(fn, from_account)
        print(f"  📝 服务已注册: '{name}' (tx={tx_hash.hex()[:16]}...)")
        return receipt

    def get_services_by_name(self, name: str) -> list:
        """按名称查询活跃服务"""
        return self.service_registry.functions.getService(name).call()

    def get_service_count(self) -> int:
        """查询服务总数"""
        return self.service_registry.functions.getServiceCount().call()

    # ── EscrowPayment 操作 ──

    def request_service(
        self, provider_address: str, service_id: int, price_eth: float, from_account: dict
    ) -> int:
        """
        Consumer 请求服务并支付（返回 requestId）
        """
        fn = self.escrow_payment.functions.requestService(
            Web3.to_checksum_address(provider_address), service_id
        )
        receipt, tx_hash = self.send_transaction(fn, from_account, value_eth=price_eth)
        # 从事件日志中解析 requestId
        logs = self.escrow_payment.events.ServiceRequested().process_receipt(receipt)
        request_id = logs[0]["args"]["requestId"]
        print(f"  💰 服务请求已发起: requestId={request_id} (tx={tx_hash.hex()[:16]}...)")
        return request_id

    def deliver_result(self, request_id: int, data_hash: bytes, from_account: dict):
        """Provider 交付结果"""
        fn = self.escrow_payment.functions.deliverResult(request_id, data_hash)
        receipt, tx_hash = self.send_transaction(fn, from_account)
        print(f"  📦 结果已交付: requestId={request_id} (tx={tx_hash.hex()[:16]}...)")
        return receipt

    def confirm_delivery(self, request_id: int, from_account: dict):
        """Consumer 确认交付（放款给 Provider）"""
        fn = self.escrow_payment.functions.confirmDelivery(request_id)
        receipt, tx_hash = self.send_transaction(fn, from_account)
        print(f"  ✅ 已确认交付: requestId={request_id} (tx={tx_hash.hex()[:16]}...)")
        return receipt

    def raise_dispute(self, request_id: int, from_account: dict):
        """Consumer 发起争议"""
        fn = self.escrow_payment.functions.dispute(request_id)
        receipt, tx_hash = self.send_transaction(fn, from_account)
        print(f"  ⚠️ 已发起争议: requestId={request_id} (tx={tx_hash.hex()[:16]}...)")
        return receipt

    def refund(self, request_id: int, from_account: dict):
        """Consumer 超时退款"""
        fn = self.escrow_payment.functions.refund(request_id)
        receipt, tx_hash = self.send_transaction(fn, from_account)
        print(f"  ↩️ 已退款: requestId={request_id} (tx={tx_hash.hex()[:16]}...)")
        return receipt

    def get_request(self, request_id: int):
        """查询支付请求详情"""
        return self.escrow_payment.functions.getRequest(request_id).call()

    def get_request_count(self) -> int:
        """查询支付请求总数"""
        return self.escrow_payment.functions.getRequestCount().call()

    # ── Reputation 操作 ──

    def rate_provider(self, provider_address: str, score: int, from_account: dict):
        """Consumer 给 Provider 打分（1-5）"""
        fn = self.reputation.functions.rateProvider(
            Web3.to_checksum_address(provider_address), score
        )
        receipt, _ = self.send_transaction(fn, from_account)
        print(f"  ⭐ 评分完成: provider={provider_address[:10]}... score={score}")
        return receipt

    def get_rating(self, provider_address: str):
        """查询 Provider 评分"""
        return self.reputation.functions.getRating(
            Web3.to_checksum_address(provider_address)
        ).call()

    # ── 事件监听 ──

    def listen_service_requested(self, callback, poll_interval: float = 2.0):
        """
        监听 ServiceRequested 事件（Provider 用）
        callback(request_id, consumer, provider, amount, service_id, deadline)
        返回一个 stop 函数。
        """
        import threading
        import time

        stop_flag = threading.Event()

        event_filter = self.escrow_payment.events.ServiceRequested.create_filter(
            fromBlock="latest"
        )

        def loop():
            while not stop_flag.is_set():
                try:
                    for event in event_filter.get_new_entries():
                        args = event["args"]
                        # 只处理发给当前 Provider 的请求
                        callback(
                            args["requestId"],
                            args["consumer"],
                            args["provider"],
                            args["amount"],
                            args["serviceId"],
                            args["deadline"],
                        )
                except Exception as e:
                    print(f"  [EventPoller] 错误: {e}")
                time.sleep(poll_interval)

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return stop_flag
