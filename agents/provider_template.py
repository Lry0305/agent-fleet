"""
Provider Agent 模板 — 独立技能 + AgentPay SDK 支付
===================================================
AgentPay 是纯支付层，不绑定 Agent 的具体技能。

每个 Provider Agent 结构:
  ┌─────────────────────────────────────┐
  │  你的独立技能模块 (任意逻辑)          │
  │  - 股票分析 / 翻译 / 天气 / 自动化   │
  │  - 完全独立，不依赖 AgentPay          │
  ├─────────────────────────────────────┤
  │  AgentPay SDK (支付能力)             │
  │  - 注册 / 登录 / 执行计费 / 链上结算  │
  │  - 导入即用，3 行代码接入             │
  └─────────────────────────────────────┘

运行方式:
    python agents/provider_template.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.agentpay_sdk import AgentPayClient

# ═══════════════════════════════════════════════════════════════
#  Part 1: 你的独立技能 — 完全不依赖 AgentPay
# ═══════════════════════════════════════════════════════════════

def my_stock_analysis(symbol: str) -> dict:
    """
    你的核心业务逻辑：股票分析。
    这部分完全独立，与 AgentPay 无关。
    可以替换成任何技能：翻译、天气、SQL查询、桌面自动化等。
    """
    import random
    return {
        "symbol": symbol.upper(),
        "price": round(random.uniform(100, 500), 2),
        "change_pct": round(random.uniform(-5, 5), 2),
        "recommendation": random.choice(["BUY", "HOLD", "SELL"]),
        "analysis": f"{symbol.upper()} 技术面偏多，成交量放大，建议关注。",
    }


def my_translation(text: str, target_lang: str = "zh") -> dict:
    """另一个独立技能：翻译服务"""
    return {
        "original": text,
        "translated": f"[{target_lang}] {text}",
        "target_lang": target_lang,
    }


# ═══════════════════════════════════════════════════════════════
#  Part 2: AgentPay SDK — 支付结算层
# ═══════════════════════════════════════════════════════════════

class StockAnalysisAgent:
    """
    一个完整的 Provider Agent。
    组合了自己的技能 (stock analysis) + AgentPay SDK (支付能力)。
    """

    def __init__(self, api_base: str = "http://127.0.0.1:8765"):
        self.client = AgentPayClient(api_base=api_base)

    # ── 注册 & 上线 ──────────────────────────────────────

    def onboard_as_api_key(self, name: str, price: float = 5.0) -> str:
        """方式 1: 用 API Key 注册上线"""
        r = self.client.register(
            name=name, role="provider",
            auth_method="api_key",
            service_name="stock_analysis",
            service_description="股票技术分析 + 买卖建议",
            price_per_call=price,
            avatar="📈",
        )
        api_key = r.get("api_key", "")
        if api_key:
            self.client.login(api_key=api_key)
            print(f"[AgentPay] ✅ 注册成功: {r['agent_id']} | API Key: {api_key}")
        return api_key

    def onboard_as_feishu(self, name: str, app_id: str, app_secret: str, price: float = 5.0) -> str:
        """方式 2: 用飞书 App ID 上线"""
        r = self.client.register(
            name=name, role="provider",
            auth_method="feishu_app",
            app_id=app_id, app_secret=app_secret,
            service_name="stock_analysis",
            service_description="股票技术分析 + 买卖建议 (飞书)",
            price_per_call=price,
            avatar="📈",
        )
        api_key = r.get("api_key", "")
        self.client.login_with_feishu(app_id=app_id, app_secret=app_secret)
        print(f"[AgentPay] ✅ 飞书注册成功: {r['agent_id']}")
        return api_key

    def onboard_as_openclaw(self, name: str, bot_token: str, price: float = 5.0) -> str:
        """方式 3: 用 OpenClaw Bot Token 上线"""
        r = self.client.register(
            name=name, role="provider",
            auth_method="openclaw_bot",
            app_id=bot_token,
            service_name="stock_analysis",
            service_description="股票技术分析 + 买卖建议 (OpenClaw)",
            price_per_call=price,
            avatar="📈",
        )
        api_key = r.get("api_key", "")
        self.client.login_with_openclaw(bot_token=bot_token)
        print(f"[AgentPay] ✅ OpenClaw 注册成功: {r['agent_id']}")
        return api_key

    # ── 执行服务 + 自动计费 ───────────────────────────────

    def serve(self, consumer_id: str, symbol: str, charge: float = None):
        """
        一次完整的服务流程:
          1. 执行自己的技能 (stock analysis)
          2. 通过 AgentPay SDK 对 Consumer 自动计费
        """
        print(f"\n{'='*50}")
        print(f"[Skill] 执行股票分析: {symbol}")
        result = my_stock_analysis(symbol)
        print(f"[Skill] 结果: {result['price']} | {result['recommendation']}")

        # AgentPay 自动计费
        if charge is None:
            charge = self.client.agent.credit_balance  # fallback
        try:
            tx = self.client.execute_task(
                task_type="data_query",
                target=f"股票分析:{symbol}",
                consumer_id=consumer_id,
                charge_amount=charge,
                parameters={"symbol": symbol},
            )
            print(f"[AgentPay] 💰 计费成功: {charge} credits | TX: {tx.get('transaction_id','')}")
        except Exception as e:
            print(f"[AgentPay] ❌ 计费失败: {e}")

        print(f"{'='*50}\n")
        return result

    def get_status(self):
        """查看当前余额和统计"""
        balance = self.client.get_balance()
        info = self.client.agent
        print(f"\n[状态] {info.avatar} {info.name}")
        print(f"  余额: {info.credit_balance} credits | 收入: {info.total_earned} | 支出: {info.total_spent}")
        if balance.get("chain_connected"):
            print(f"  链上 ETH: {balance.get('eth_onchain', 0)}")
        return balance


# ═══════════════════════════════════════════════════════════════
#  示例: 启动 Provider Agent
# ═══════════════════════════════════════════════════════════════

def demo():
    """
    演示一个完整的 Provider Agent 生命周期。
    """
    agent = StockAnalysisAgent()

    # Step 1: 上线 (选一种方式)
    # 方式 A: API Key
    api_key = agent.onboard_as_api_key(name="StockMaster", price=5.0)

    # 方式 B: 飞书 (需要真实的 App ID/Secret)
    # agent.onboard_as_feishu("FeishuStockBot", app_id="cli_xxx", app_secret="xxx")

    # 方式 C: OpenClaw (需要真实的 Bot Token)
    # agent.onboard_as_openclaw("OCStockBot", bot_token="bot_xxx")

    # Step 2: 查看状态
    agent.get_status()

    # Step 3: 等待 Consumer 选择你并发起支付后，
    #         执行服务 → 自动计费
    print("\n💡 等待 Consumer 发起支付后，调用 agent.serve(consumer_id, 'AAPL') 即可自动计费")
    print(f"💡 你的 API Key: {api_key}")
    print(f"💡 消费者可以用这个 API Key 找到你并发起交易")


if __name__ == "__main__":
    demo()
