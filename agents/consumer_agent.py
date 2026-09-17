"""
Consumer Agent (LangGraph)
==========================
买方 Agent：发现服务 → 检查余额 → 发起托管支付 → 等待交付 → 验证数据 → 确认/争议 → 评价

场景：股票分析 Agent（Bob）需要查询财报数据，自动调用该流程。
"""

import time
import hashlib
from typing import TypedDict

from langgraph.graph import StateGraph, END

from agents.web3_client import Web3Client
from agents.config import (
    BOB_PRIVATE_KEY,
    SERVICE_NAME,
    SERVICE_PRICE_ETH,
    TIMEOUT_SECONDS,
)


# ── Agent 状态定义 ──

class ConsumerState(TypedDict):
    """Consumer Agent 的状态图"""
    # 输入参数
    service_name: str          # 要查询的服务名称
    max_price_eth: float       # 愿意支付的最高价格

    # 中间状态
    provider_address: str      # 选中的提供商地址
    provider_name: str         # 提供商标识（用于日志）
    service_id: int            # 服务 ID
    actual_price_eth: float    # 实际价格
    request_id: int            # 支付请求 ID
    expected_data_hash: str    # Agent 根据业务逻辑算出的预期哈希

    # 结果
    data_delivered: bool       # 数据是否已交付
    data_verified: bool        # 数据是否验证通过
    confirmed: bool            # 是否已确认
    rated: bool                # 是否已评价

    # 流程控制
    error: str                 # 错误信息
    phase: str                 # 当前阶段（日志用）


# ── 节点函数 ──

def discover_service(state: ConsumerState, client: Web3Client) -> dict:
    """
    节点 1：查询 ServiceRegistry 发现匹配服务。
    选择第一个满足价格要求的活跃服务。
    """
    print(f"\n{'='*50}")
    print(f"🔍 [Consumer] 正在查询服务: '{state['service_name']}'")
    print(f"{'='*50}")

    services = client.get_services_by_name(state["service_name"])

    if len(services) == 0:
        return {"error": f"未找到服务: {state['service_name']}", "phase": "discover_failed"}

    # 选择价格最低且不超过预算的服务
    candidates = []
    for s in services:
        price_eth = float(client.w3.from_wei(s[3], "ether"))
        if price_eth <= state["max_price_eth"]:
            candidates.append((s, price_eth))

    if not candidates:
        cheapest = float(client.w3.from_wei(min(s[3] for s in services), "ether"))
        return {"error": f"服务价格超出预算 (最便宜 {cheapest} ETH)", "phase": "discover_failed"}

    # 选第一个候选
    svc, price = candidates[0]
    provider_addr = svc[1]
    service_id = svc[0]
    provider_name = f"Provider_{provider_addr[:8]}"

    print(f"  📋 找到服务: id={service_id}")
    print(f"  👤 供应商:   {provider_addr}")
    print(f"  💲 价格:     {price} ETH")

    return {
        "provider_address": provider_addr,
        "provider_name": provider_name,
        "service_id": service_id,
        "actual_price_eth": price,
        "phase": "discovered",
    }


def check_balance(state: ConsumerState, client: Web3Client) -> dict:
    """节点 2：检查 Consumer 钱包余额"""
    print(f"\n💰 [Consumer] 检查钱包余额...")

    acct = client.get_account(BOB_PRIVATE_KEY)
    balance = client.get_balance(acct["address"])
    needed = state["actual_price_eth"]

    print(f"  💳 余额:     {balance:.4f} ETH")
    print(f"  💸 需要:     {needed:.4f} ETH")

    if balance < needed:
        return {"error": f"余额不足 (有 {balance} ETH, 需要 {needed} ETH)", "phase": "balance_insufficient"}

    # 预留一点 Gas 费（约 0.001 ETH）
    if balance < needed + 0.001:
        return {"error": f"余额不足以覆盖 Gas 费", "phase": "balance_insufficient"}

    return {"phase": "balance_ok"}


def request_service(state: ConsumerState, client: Web3Client) -> dict:
    """节点 3：发起托管支付，资金锁定在合约中"""
    print(f"\n💸 [Consumer] 发起托管支付...")

    acct = client.get_account(BOB_PRIVATE_KEY)

    try:
        request_id = client.request_service(
            state["provider_address"],
            state["service_id"],
            state["actual_price_eth"],
            acct,
        )
        print(f"  🔒 资金已锁定: requestId={request_id}")
        return {"request_id": request_id, "phase": "payment_locked"}
    except Exception as e:
        return {"error": f"支付失败: {e}", "phase": "payment_failed"}


def wait_delivery(state: ConsumerState, client: Web3Client) -> dict:
    """节点 4：轮询等待 Provider 交付结果"""
    print(f"\n⏳ [Consumer] 等待 Provider 交付结果 (最多 {TIMEOUT_SECONDS}s)...")

    request_id = state["request_id"]
    start_time = time.time()

    while time.time() - start_time < TIMEOUT_SECONDS:
        req = client.get_request(request_id)
        # state 字段索引: id=0, consumer=1, provider=2, amount=3,
        #                 dataHash=4, state=5, deadline=6, serviceId=7
        current_state = req[5]  # enum: 0=Pending, 1=Delivered, 2=Confirmed, 3=Disputed, 4=Refunded

        if current_state == 1:  # Delivered
            data_hash = req[4]
            print(f"  📦 收到交付: hash=0x{data_hash.hex()[:16]}...")
            return {
                "data_delivered": True,
                "expected_data_hash": "0x" + data_hash.hex(),
                "phase": "delivery_received",
            }
        elif current_state >= 2:  # Confirmed/Disputed/Refunded (不应该在等待阶段出现)
            print(f"  ⚠️ 请求状态异常: state={current_state}")
            return {"error": f"状态异常: {current_state}", "phase": "state_error"}

        elapsed = time.time() - start_time
        print(f"  ... 等待中 ({elapsed:.0f}s)", end="\r")

    print(f"\n  ⏰ 超时！Provider 未在 {TIMEOUT_SECONDS}s 内交付")
    return {"error": "Provider 超时未交付", "phase": "delivery_timeout"}


def verify_data(state: ConsumerState, client: Web3Client) -> dict:
    """
    节点 5：验证 Provider 交付的数据
    现实中：Consumer Agent 会从链下通道获取原始数据（JSON），
    计算哈希与链上 dataHash 比对。这里用模拟数据。
    """
    print(f"\n🔬 [Consumer] 验证数据完整性...")

    # 模拟：Consumer 从链下收到数据后计算哈希
    simulated_received_data = "AAPL Q4 2024 EPS: $2.35"
    computed_hash = "0x" + hashlib.sha256(simulated_received_data.encode()).hexdigest()

    # 简化：用 keccak256（需要 web3）
    from web3 import Web3 as W3
    computed_hash_w3 = W3.keccak(text=simulated_received_data).hex()

    chain_hash = state["expected_data_hash"]

    print(f"  📊 链上哈希:  {chain_hash[:18]}...")
    print(f"  📊 计算哈希:  {computed_hash_w3[:18]}...")

    if chain_hash == "0x" + computed_hash_w3:
        print(f"  ✅ 数据验证通过！完整性正确")
        return {"data_verified": True, "phase": "verified"}
    else:
        print(f"  ❌ 数据验证失败！哈希不匹配")
        return {"data_verified": False, "phase": "verification_failed"}


def confirm_delivery(state: ConsumerState, client: Web3Client) -> dict:
    """节点 6：确认交付 → 合约放款给 Provider"""
    print(f"\n✅ [Consumer] 确认交付，准备放款...")

    acct = client.get_account(BOB_PRIVATE_KEY)
    try:
        client.confirm_delivery(state["request_id"], acct)
        return {"confirmed": True, "phase": "confirmed"}
    except Exception as e:
        return {"error": f"确认失败: {e}", "phase": "confirm_failed"}


def raise_dispute(state: ConsumerState, client: Web3Client) -> dict:
    """节点 6b：数据有问题 → 发起争议"""
    print(f"\n⚠️ [Consumer] 数据异常，发起争议...")

    acct = client.get_account(BOB_PRIVATE_KEY)
    try:
        client.raise_dispute(state["request_id"], acct)
        return {"phase": "disputed"}
    except Exception as e:
        return {"error": f"争议发起失败: {e}", "phase": "dispute_failed"}


def do_refund(state: ConsumerState, client: Web3Client) -> dict:
    """节点 6c：超时退款"""
    print(f"\n↩️ [Consumer] 超时退款...")

    acct = client.get_account(BOB_PRIVATE_KEY)
    try:
        client.refund(state["request_id"], acct)
        return {"phase": "refunded"}
    except Exception as e:
        return {"error": f"退款失败: {e}", "phase": "refund_failed"}


def rate_provider(state: ConsumerState, client: Web3Client) -> dict:
    """节点 7：给 Provider 打分"""
    print(f"\n⭐ [Consumer] 给 Provider 打分...")

    acct = client.get_account(BOB_PRIVATE_KEY)
    try:
        client.rate_provider(state["provider_address"], 5, acct)
        avg, total, jobs = client.get_rating(state["provider_address"])
        print(f"  📊 Provider 当前评分: {avg}/5 ({total} 次评价, {jobs} 单完成)")
        return {"rated": True, "phase": "completed"}
    except Exception as e:
        return {"error": f"评价失败: {e}", "phase": "rate_failed"}


# ── 路由函数 ──

def route_after_discover(state: ConsumerState) -> str:
    if state.get("error"):
        return END
    return "check_balance"


def route_after_balance(state: ConsumerState) -> str:
    if state.get("error"):
        return END
    return "request_service"


def route_after_payment(state: ConsumerState) -> str:
    if state.get("error"):
        return END
    return "wait_delivery"


def route_after_wait(state: ConsumerState) -> str:
    if state.get("phase") == "delivery_received":
        return "verify_data"
    elif state.get("phase") == "delivery_timeout":
        return "do_refund"
    return END


def route_after_verify(state: ConsumerState) -> str:
    if state.get("data_verified"):
        return "confirm_delivery"
    return "raise_dispute"


# ── 构建图 ──

def build_consumer_graph(client: Web3Client) -> StateGraph:
    """构建 Consumer Agent 的 LangGraph 状态图"""

    workflow = StateGraph(ConsumerState)

    # 添加节点
    workflow.add_node("discover_service", lambda s: discover_service(s, client))
    workflow.add_node("check_balance", lambda s: check_balance(s, client))
    workflow.add_node("request_service", lambda s: request_service(s, client))
    workflow.add_node("wait_delivery", lambda s: wait_delivery(s, client))
    workflow.add_node("verify_data", lambda s: verify_data(s, client))
    workflow.add_node("confirm_delivery", lambda s: confirm_delivery(s, client))
    workflow.add_node("raise_dispute", lambda s: raise_dispute(s, client))
    workflow.add_node("do_refund", lambda s: do_refund(s, client))
    workflow.add_node("rate_provider", lambda s: rate_provider(s, client))

    # 设置入口
    workflow.set_entry_point("discover_service")

    # 添加条件边
    workflow.add_conditional_edges("discover_service", route_after_discover)
    workflow.add_conditional_edges("check_balance", route_after_balance)
    workflow.add_conditional_edges("request_service", route_after_payment)
    workflow.add_conditional_edges("wait_delivery", route_after_wait)
    workflow.add_conditional_edges("verify_data", route_after_verify)

    # 添加固定边
    workflow.add_edge("confirm_delivery", "rate_provider")
    workflow.add_edge("rate_provider", END)
    workflow.add_edge("raise_dispute", END)
    workflow.add_edge("do_refund", END)

    return workflow.compile()


# ── 便捷调用 ──

def run_consumer(
    client: Web3Client,
    service_name: str = SERVICE_NAME,
    max_price_eth: float = SERVICE_PRICE_ETH,
) -> dict:
    """运行 Consumer Agent 的完整流程"""
    graph = build_consumer_graph(client)

    initial_state: ConsumerState = {
        "service_name": service_name,
        "max_price_eth": max_price_eth,
        "provider_address": "",
        "provider_name": "",
        "service_id": 0,
        "actual_price_eth": 0.0,
        "request_id": 0,
        "expected_data_hash": "",
        "data_delivered": False,
        "data_verified": False,
        "confirmed": False,
        "rated": False,
        "error": "",
        "phase": "start",
    }

    final_state = graph.invoke(initial_state)
    return final_state
