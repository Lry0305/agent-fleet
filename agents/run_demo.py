#!/usr/bin/env python3
"""
AgentPay — 完整流程演示
=======================
同时运行 Provider Agent 和 Consumer Agent，
演示 Agent 间的自动服务发现、托管支付、交付验证全流程。

    终端 1: npx hardhat node          (启动本地链)
    终端 2: npx hardhat deploy ...     (部署合约)
    终端 3: python agents/run_demo.py  (运行本脚本)

或使用一键脚本:
    ./run_all.sh
"""

import os
import sys
import threading
import time
from agents.config import (
    RPC_URL,
    SERVICE_REGISTRY_ADDRESS,
    ESCROW_PAYMENT_ADDRESS,
    REPUTATION_ADDRESS,
    SERVICE_NAME,
    SERVICE_PRICE_ETH,
)
from agents.web3_client import Web3Client
from agents.provider_agent import run_provider
from agents.consumer_agent import run_consumer


def check_prerequisites(client: Web3Client):
    """检查前置条件：合约是否已部署"""
    print("🔧 检查前置条件...")
    print(f"   RPC:            {RPC_URL}")
    print(f"   ServiceRegistry: {SERVICE_REGISTRY_ADDRESS or '(未设置)'}")
    print(f"   EscrowPayment:   {ESCROW_PAYMENT_ADDRESS or '(未设置)'}")
    print(f"   Reputation:      {REPUTATION_ADDRESS or '(未设置)'}")

    if not all([SERVICE_REGISTRY_ADDRESS, ESCROW_PAYMENT_ADDRESS, REPUTATION_ADDRESS]):
        print("\n❌ 错误：合约地址未设置！")
        print("   请先运行: npx hardhat run scripts/deploy.js --network localhost")
        print("   然后将输出的合约地址填入 agents/config.py")
        return False

    try:
        count = client.get_service_count()
        req_count = client.get_request_count()
        print(f"   ✅ 服务数: {count}, 支付请求数: {req_count}")
        return True
    except Exception as e:
        print(f"   ❌ 合约调用失败: {e}")
        return False


def demo_single_flow():
    """
    单次完整流程演示：
    Provider 先注册服务 → Consumer 发起支付 → Provider 交付 → Consumer 确认
    """
    print("\n" + "=" * 60)
    print("  🚀 AgentPay — Agent 间自动支付流程演示")
    print("=" * 60)

    client = Web3Client()

    if not check_prerequisites(client):
        sys.exit(1)

    # 查询双方余额
    from agents.config import ALICE_PRIVATE_KEY, BOB_PRIVATE_KEY
    alice_acct = client.get_account(ALICE_PRIVATE_KEY)
    bob_acct = client.get_account(BOB_PRIVATE_KEY)

    alice_bal = client.get_balance(alice_acct["address"])
    bob_bal = client.get_balance(bob_acct["address"])

    print(f"\n👤 Alice (Provider): {alice_acct['address']}  余额 {alice_bal:.4f} ETH")
    print(f"👤 Bob   (Consumer): {bob_acct['address']}  余额 {bob_bal:.4f} ETH")

    # ── 并行运行 Provider 和 Consumer ──
    print(f"\n{'~'*60}")
    print(f"  📋 场景: Bob 需要 '{SERVICE_NAME}' 服务, 预算 {SERVICE_PRICE_ETH} ETH")
    print(f"{'~'*60}")

    # Provider 在后台线程运行（先注册，然后等待请求）
    provider_result = {"state": None, "error": None}

    def provider_thread_fn():
        try:
            provider_result["state"] = run_provider(
                client, SERVICE_NAME, "查询美股上市公司的财报核心指标", SERVICE_PRICE_ETH
            )
        except Exception as e:
            provider_result["error"] = str(e)

    provider_thread = threading.Thread(target=provider_thread_fn, daemon=True)
    provider_thread.start()

    # 等 Provider 注册完成
    time.sleep(3)

    # Consumer 在主线程运行
    consumer_state = run_consumer(client, SERVICE_NAME, SERVICE_PRICE_ETH)

    # 等待 Provider 线程结束
    provider_thread.join(timeout=30)

    # ── 打印结果 ──
    print(f"\n{'='*60}")
    print(f"  📊 流程结果")
    print(f"{'='*60}")

    print(f"\n  Consumer 最终状态:")
    print(f"    阶段:   {consumer_state.get('phase', 'unknown')}")
    print(f"    错误:   {consumer_state.get('error') or '无'}")
    print(f"    已确认: {consumer_state.get('confirmed')}")
    print(f"    已评价: {consumer_state.get('rated')}")

    if provider_result["state"]:
        print(f"\n  Provider 最终状态:")
        print(f"    阶段:   {provider_result['state'].get('phase', 'unknown')}")
        print(f"    错误:   {provider_result['state'].get('error') or '无'}")
        print(f"    已确认: {provider_result['state'].get('confirmed')}")
    elif provider_result["error"]:
        print(f"\n  Provider 错误: {provider_result['error']}")

    # 最终余额
    alice_bal2 = client.get_balance(alice_acct["address"])
    bob_bal2 = client.get_balance(bob_acct["address"])
    print(f"\n  💰 最终余额:")
    print(f"    Alice: {alice_bal2:.4f} ETH (变化: {alice_bal2 - alice_bal:+.4f})")
    print(f"    Bob:   {bob_bal2:.4f} ETH (变化: {bob_bal2 - bob_bal:+.4f})")

    # 链上状态
    try:
        if consumer_state.get("request_id", 0) > 0 or True:
            req_count = client.get_request_count()
            if req_count > 0:
                req = client.get_request(req_count - 1)
                state_names = ["Pending", "Delivered", "Confirmed", "Disputed", "Refunded"]
                print(f"\n  📜 链上记录 (requestId={req[0]}):")
                print(f"    Consumer:  {req[1][:16]}...")
                print(f"    Provider:  {req[2][:16]}...")
                print(f"    Amount:    {client.w3.from_wei(req[3], 'ether')} ETH")
                print(f"    State:     {state_names[req[5]]}")
    except Exception:
        pass

    print(f"\n{'='*60}")
    if consumer_state.get("phase") == "completed":
        print(f"  ✅ 流程成功完成！Agent 间自动交易验证通过")
    else:
        print(f"  ⚠️ 流程未完全完成，请检查上方日志")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    demo_single_flow()
