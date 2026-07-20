"""
完整示例：Provider 上线 + 等待支付 + CUA-Driver 执行 + 链上结算
=================================================================
演示一个完整的 Provider Agent 生命周期。
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agents.agentpay_sdk import AgentPayClient
from agentpay_skill.cua_runner import CUARunner
from agentpay_skill.wallet import AgentWallet
from agentpay_skill.skills import create_default_registry


def main():
    print("=" * 60)
    print("  AgentPay Skill — Provider 完整演示")
    print("=" * 60)

    client = AgentPayClient(api_base="http://127.0.0.1:8765")

    # ═══ Step 1: 创建链上钱包 ═══
    print("\n[1/5] 创建链上钱包...")
    wallet = AgentWallet(name="demo_provider")
    print(f"  ETH 地址: {wallet.address}")

    # ═══ Step 2: 注册 & 登录 ═══
    print("\n[2/5] 注册 Provider...")
    result = client.register(
        name="DemoStockMaster",
        role="provider",
        auth_method="api_key",
        service_name="stock_analysis",
        service_description="股票技术分析 (CUA-Driver 浏览器搜索)",
        price_per_call=5.0,
        avatar="📈",
    )
    api_key = result["api_key"]
    agent_id = result["agent_id"]
    print(f"  Agent ID: {agent_id}")
    print(f"  API Key:  {api_key}")
    print(f"  Credits:  {result['credit_balance']}")

    agent = client.login(api_key=api_key)
    print(f"  登录成功: {agent.name}")

    # ═══ Step 3: 初始化 Skill & CUA ═══
    print("\n[3/5] 初始化 Skill & CUA-Driver...")
    registry = create_default_registry()
    skill = registry.get("stock_analysis")
    cua = CUARunner()
    print(f"  Skill: {skill.name} ({skill.description})")
    print(f"  CUA-Driver: {'可用 ✅' if cua.available else '模拟 ⚠️'}")

    # ═══ Step 4: 等待 Consumer 支付 ═══
    print("\n[4/5] 等待 Consumer 支付...")
    print(f"  💡 在另一个终端运行:")
    print(f"     cd agentpay-skill")
    print(f"     python -m agentpay_skill.cli pay --provider {agent_id} --amount 5")

    # 轮询交易
    txs_before = 0
    while True:
        txs = client.get_transactions(limit=5)
        if len(txs) > txs_before and txs[0].get("status") == "fund_locked":
            tx = txs[0]
            consumer_id = tx["consumer_id"]
            print(f"\n  📨 收到支付！Consumer: {tx.get('consumer_name')}, 金额: {tx.get('amount')} cr")
            break
        time.sleep(3)

    # ═══ Step 5: 执行 + 计费 ═══
    print("\n[5/5] CUA-Driver 执行 + SDK 自动计费...")

    # CUA-Driver 执行任务
    cua_result = cua.execute(
        task_type=skill.task_type,
        target="AAPL 股票分析",
    )
    print(f"  CUA 执行结果: {'成功' if cua_result.get('success') else '失败'}")

    # AgentPay SDK 自动计费
    tx = client.execute_task(
        task_type=skill.task_type,
        target="AAPL 股票分析",
        consumer_id=consumer_id,
        charge_amount=skill.price,
        parameters={"cua_result": cua_result},
    )
    print(f"  💰 计费完成: {tx.get('transaction_id')}")
    print(f"  Provider 余额: {tx.get('provider_balance')} credits")

    # 显示结果
    print("\n" + "=" * 60)
    print("  🎉 完整流程完成！")
    print(f"  Provider: {agent.name} (余额: {tx.get('provider_balance')} cr)")
    print(f"  ETH 地址: {wallet.address}")
    print("=" * 60)


if __name__ == "__main__":
    main()
