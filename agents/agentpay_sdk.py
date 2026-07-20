"""
AgentPay SDK v3.0 — Agent 通用支付层
=====================================
AgentPay 是纯支付/结算层。每个 Agent 自带技能，导入 SDK 获得支付能力。

三种认证方式:
  - api_key:      Web Agent，用 ap_sk_xxx
  - feishu_app:   飞书机器人，用 App ID + App Secret
  - openclaw_bot: OpenClaw 机器人，用 Bot Token

快速开始:
    from agents.agentpay_sdk import AgentPayClient
    client = AgentPayClient()

    # ── 方式 1: API Key ──
    result = client.register("StockBot", "provider", service_name="stock_query", price=5)
    client.login(api_key=result["api_key"])

    # ── 方式 2: 飞书 App ──
    result = client.register("FeishuBot", "provider",
        auth_method="feishu_app", app_id="cli_abc", app_secret="secret123")
    client.login_with_feishu(app_id="cli_abc", app_secret="secret123")

    # ── 方式 3: OpenClaw Bot ──
    result = client.register("OpenClawBot", "consumer",
        auth_method="openclaw_bot", app_id="bot_token_xxx")
    client.login_with_openclaw(bot_token="bot_token_xxx")

    # ── 通用操作 ──
    tx = client.pay(provider_id="ag_xxx", amount=10)
    client.confirm(tx.transaction_id)
    client.execute_task("data_query", "查AAPL", consumer_id="ag_xxx", charge_amount=5)
"""

import requests
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class AgentInfo:
    agent_id: str = ""
    name: str = ""
    role: str = ""
    credit_balance: float = 0.0
    total_earned: float = 0.0
    total_spent: float = 0.0
    avatar: str = "🤖"


@dataclass
class TransactionResult:
    transaction_id: str = ""
    amount: float = 0.0
    status: str = ""
    provider_name: str = ""
    consumer_name: str = ""
    chain_tx_hash: str = ""
    message: str = ""


class AgentPayClient:
    """
    AgentPay 通用客户端。
    支持 Agent 注册、登录、支付、执行、查询。
    """

    def __init__(self, api_base: str = "http://127.0.0.1:8765"):
        self.api_base = api_base.rstrip("/")
        self.api_key: str = ""
        self._agent: Optional[AgentInfo] = None

    # ── 内部 ────────────────────────────────────────────

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _get(self, path: str) -> dict:
        r = requests.get(f"{self.api_base}{path}", headers=self._headers(), timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}

    def _post(self, path: str, data: dict = None) -> dict:
        r = requests.post(f"{self.api_base}{path}", headers=self._headers(), json=data or {}, timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}

    # ── 公开方法 ─────────────────────────────────────────

    @property
    def agent(self) -> Optional[AgentInfo]:
        return self._agent

    def register(
        self,
        name: str,
        role: str,
        *,
        auth_method: str = "api_key",
        app_id: str = "",
        app_secret: str = "",
        service_name: str = "",
        service_description: str = "",
        price_per_call: float = 0.0,
        description: str = "",
        avatar: str = "🤖",
        platform: str = "web",
    ) -> dict:
        """
        注册新 Agent。支持三种认证方式:
          - api_key:      自动生成 ap_sk_xxx
          - feishu_app:   需提供 app_id + app_secret (飞书 App ID + App Secret)
          - openclaw_bot: 需提供 app_id (Bot Token)

        返回包含 api_key 的字典。无论哪种认证方式都会生成 API Key 备用。
        ⚠️ 请妥善保存 api_key / app_secret。
        """
        payload = {
            "name": name, "role": role, "auth_method": auth_method,
            "avatar": avatar, "description": description, "platform": platform,
        }
        if auth_method in ("feishu_app", "openclaw_bot"):
            payload["app_id"] = app_id
            if app_secret:
                payload["app_secret"] = app_secret
        if role == "provider":
            payload.update({
                "service_name": service_name,
                "service_description": service_description,
                "price_per_call": price_per_call,
            })
        return self._post("/api/agents/register", payload)

    def _login_with_result(self, result: dict) -> AgentInfo:
        """内部: 解析登录结果并设置认证令牌"""
        api_key = result.get("api_key", "")
        if api_key:
            self.api_key = api_key
        self._agent = AgentInfo(
            agent_id=result.get("agent_id", ""),
            name=result.get("name", ""),
            role=result.get("role", ""),
            credit_balance=result.get("credit_balance", 0),
            total_earned=result.get("total_earned", 0),
            total_spent=result.get("total_spent", 0),
            avatar=result.get("avatar", "🤖"),
        )
        return self._agent

    def login(self, api_key: str = "") -> AgentInfo:
        """
        通过 API Key 登录 (ap_sk_xxx)。
        登录后才能调用 pay/confirm/execute 等需要身份的方法。

        也可以不传 api_key 直接设置 self.api_key 后调用。
        """
        if api_key:
            self.api_key = api_key
        result = self._post("/api/agents/login", {
            "auth_method": "api_key",
            "api_key": self.api_key,
        })
        if result.get("error"):
            raise ValueError(f"登录失败: {result.get('detail')}")
        return self._login_with_result(result)

    def login_with_feishu(self, app_id: str, app_secret: str) -> AgentInfo:
        """
        通过飞书 App ID + App Secret 登录。
        适用于飞书机器人 Agent 身份认证。

        Args:
            app_id: 飞书应用的 App ID (如 cli_xxx)
            app_secret: 飞书应用的 App Secret
        Returns:
            AgentInfo: 登录后的 Agent 信息
        """
        result = self._post("/api/agents/login", {
            "auth_method": "feishu_app",
            "app_id": app_id,
            "app_secret": app_secret,
        })
        if result.get("error"):
            raise ValueError(f"飞书登录失败: {result.get('detail')}")
        return self._login_with_result(result)

    def login_with_openclaw(self, bot_token: str) -> AgentInfo:
        """
        通过 OpenClaw Bot Token 登录。
        适用于 OpenClaw 机器人身份认证。

        Args:
            bot_token: OpenClaw Bot 的 Token
        Returns:
            AgentInfo: 登录后的 Agent 信息
        """
        result = self._post("/api/agents/login", {
            "auth_method": "openclaw_bot",
            "bot_token": bot_token,
        })
        if result.get("error"):
            raise ValueError(f"OpenClaw 登录失败: {result.get('detail')}")
        return self._login_with_result(result)

    def get_balance(self) -> dict:
        """查询当前 Agent 的 credit 余额和 ETH 等值"""
        return self._get("/api/billing/balance")

    def list_agents(self, role: str = "") -> list:
        """浏览平台上所有公开 Agent"""
        params = {}
        if role:
            params["role"] = role
        result = self._get("/api/agents/all")
        return result.get("agents", [])

    def pay(self, provider_id: str, amount: float, service_id: str = "") -> TransactionResult:
        """
        【Consumer】发起支付：锁定 credits → 创建链上 Escrow。
        资金锁定在智能合约中，Provider 暂时无法取走。
        """
        data = {"provider_id": provider_id, "amount": amount}
        if service_id:
            data["service_id"] = service_id
        r = self._post("/api/billing/pay", data)
        if r.get("error"):
            raise RuntimeError(f"支付失败: {r.get('detail')}")
        return TransactionResult(
            transaction_id=r.get("transaction_id", ""),
            amount=amount,
            status=r.get("status", "fund_locked"),
            provider_name=r.get("provider_name", ""),
            chain_tx_hash=r.get("chain_tx_hash", ""),
            message=r.get("message", ""),
        )

    def confirm(self, tx_id: str) -> dict:
        """
        【Consumer】确认交付 → 链上合约释放 ETH 给 Provider。
        不可逆操作，确认后资金立即转入 Provider。
        """
        return self._post(f"/api/billing/confirm/{tx_id}")

    def execute_task(
        self,
        task_type: str,
        target: str,
        *,
        consumer_id: str,
        charge_amount: float,
        service_id: str = "",
        parameters: dict = None,
    ) -> dict:
        """
        【Provider】执行任务并自动计费。
        执行完成后自动从 Consumer 扣款转入 Provider。
        """
        payload = {
            "task_type": task_type,
            "target": target,
            "consumer_id": consumer_id,
            "charge_amount": charge_amount,
            "service_id": service_id,
            "parameters": parameters or {},
        }
        return self._post("/api/openclaw/execute", payload)

    def get_transactions(self, limit: int = 20) -> list:
        """查询当前 Agent 的交易记录"""
        r = self._get(f"/api/billing/transactions?limit={limit}")
        return r.get("transactions", [])

    def get_stats(self) -> dict:
        """查询平台统计数据"""
        return self._get("/api/billing/stats")

    def register_service(self, name: str, description: str = "", price: float = 0.0) -> dict:
        """【Provider】注册服务到市场"""
        return self._post("/api/services/register", {
            "name": name, "description": description, "price": price,
        })

    def bind_robot(self, bot_token: str, platform: str = "openclaw", webhook_url: str = "") -> dict:
        """【Provider】绑定 OpenClaw/飞书 机器人"""
        return self._post("/api/openclaw/register-bot", {
            "bot_token": bot_token, "platform": platform, "webhook_url": webhook_url,
        })
