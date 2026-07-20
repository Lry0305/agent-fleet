"""
Provider Agent (LangGraph)
==========================
卖方 Agent：注册服务 → 监听请求 → 执行查询 → 交付结果 → 等待收款

场景：金融数据 Agent（Alice）注册"财报查询"服务，收到请求后查询数据并交付。
"""

import time
from typing import TypedDict
from queue import Queue

from langgraph.graph import StateGraph, END

from agents.web3_client import Web3Client
from agents.config import (
    ALICE_PRIVATE_KEY,
    SERVICE_NAME,
    SERVICE_PRICE_ETH,
    POLL_INTERVAL,
)


# ── Agent 状态定义 ──

class ProviderState(TypedDict):
    """Provider Agent 的状态图"""
    # 服务注册信息
    service_name: str
    service_description: str
    price_eth: float

    # 当前处理的请求
    current_request_id: int
    current_consumer: str
    current_amount: int       # wei
    current_service_id: int

    # 查询结果
    query_result: str         # 链下返回的原始数据
    data_hash: str            # 链上存证的哈希

    # 流程控制
    registered: bool          # 服务是否已注册
    delivered: bool           # 数据是否已交付
    confirmed: bool           # Consumer 是否已确认
    error: str
    phase: str


# ── 事件队列（Provider 监听链上事件用）──

_event_queue: Queue = Queue()


def _on_service_requested(request_id, consumer, provider, amount, service_id, deadline):
    """ServiceRequested 事件回调 → 推入队列供 Agent 处理"""
    _event_queue.put({
        "request_id": request_id,
        "consumer": consumer,
        "provider": provider,
        "amount": amount,
        "service_id": service_id,
        "deadline": deadline,
    })
    print(f"  🔔 收到新请求: requestId={request_id} consumer={consumer[:10]}...")


# ── 节点函数 ──

def register_service(state: ProviderState, client: Web3Client) -> dict:
    """节点 1：在 ServiceRegistry 注册服务"""
    print(f"\n{'='*50}")
    print(f"📝 [Provider] 注册服务: '{state['service_name']}'")
    print(f"{'='*50}")

    acct = client.get_account(ALICE_PRIVATE_KEY)

    try:
        client.register_service(
            state["service_name"],
            state["service_description"],
            state["price_eth"],
            acct,
        )
        services = client.get_services_by_name(state["service_name"])
        print(f"  ✅ 注册成功！当前共有 {len(services)} 个 '{state['service_name']}' 服务")
        return {"registered": True, "phase": "registered"}
    except Exception as e:
        return {"error": f"注册失败: {e}", "phase": "register_failed"}


def wait_request(state: ProviderState, client: Web3Client) -> dict:
    """
    节点 2：启动事件监听器，等待 Consumer 发来的支付请求。
    收到 ServiceRequested 事件后进入执行阶段。
    """
    print(f"\n👂 [Provider] 开始监听服务请求...")

    # 启动事件监听（非阻塞）
    stop_flag = client.listen_service_requested(
        _on_service_requested,
        poll_interval=POLL_INTERVAL,
    )

    # 等待事件到达（阻塞）
    print(f"  ⏳ 等待 Consumer 发起支付请求...")
    try:
        event = _event_queue.get(timeout=120)  # 最多等 2 分钟
        stop_flag.set()  # 停止监听

        print(f"  📥 收到支付请求:")
        print(f"     requestId:    {event['request_id']}")
        print(f"     consumer:     {event['consumer']}")
        print(f"     amount:       {client.w3.from_wei(event['amount'], 'ether')} ETH")
        print(f"     serviceId:    {event['service_id']}")

        return {
            "current_request_id": event["request_id"],
            "current_consumer": event["consumer"],
            "current_amount": event["amount"],
            "current_service_id": event["service_id"],
            "phase": "request_received",
        }

    except Exception as e:
        stop_flag.set()
        return {"error": f"等待请求超时: {e}", "phase": "wait_timeout"}


def execute_service(state: ProviderState, client: Web3Client) -> dict:
    """
    节点 3：执行查询任务（链下计算）
    现实中：调用 API、查询数据库、运行模型等。
    这里模拟：生成一份财报数据并计算哈希。
    """
    print(f"\n⚙️ [Provider] 执行查询任务...")

    # 模拟：实际场景中这里调 MCP、API 等
    simulated_data = "AAPL Q4 2024 EPS: $2.35"
    print(f"  📊 查询结果: {simulated_data}")

    # 计算哈希（上链存证用）
    from web3 import Web3 as W3
    data_hash_bytes = W3.keccak(text=simulated_data)
    data_hash = "0x" + data_hash_bytes.hex()

    print(f"  🔐 数据哈希: {data_hash[:18]}...")

    # 模拟处理时间
    time.sleep(1)

    return {
        "query_result": simulated_data,
        "data_hash": data_hash,
        "phase": "executed",
    }


def deliver_result(state: ProviderState, client: Web3Client) -> dict:
    """节点 4：提交数据哈希到链上（deliverResult）"""
    print(f"\n📦 [Provider] 交付结果到链上...")

    acct = client.get_account(ALICE_PRIVATE_KEY)

    try:
        data_hash_bytes = bytes.fromhex(state["data_hash"].replace("0x", ""))
        client.deliver_result(
            state["current_request_id"],
            data_hash_bytes,
            acct,
        )
        return {"delivered": True, "phase": "delivered"}
    except Exception as e:
        return {"error": f"交付失败: {e}", "phase": "deliver_failed"}


def wait_confirmation(state: ProviderState, client: Web3Client) -> dict:
    """
    节点 5：等待 Consumer 确认（轮询请求状态）
    Consumer 确认后合约自动放款，Provider 收到 ETH。
    """
    print(f"\n💤 [Provider] 等待 Consumer 确认放款...")

    request_id = state["current_request_id"]
    start_time = time.time()

    while time.time() - start_time < 60:
        req = client.get_request(request_id)
        current_state = req[5]  # 0=Pending, 1=Delivered, 2=Confirmed, 3=Disputed, 4=Refunded

        if current_state == 2:  # Confirmed → 已放款
            provider_addr = client.get_account(ALICE_PRIVATE_KEY)["address"]
            balance = client.get_balance(provider_addr)
            print(f"  💰 已收到付款！当前余额: {balance:.4f} ETH")
            return {"confirmed": True, "phase": "completed"}

        elif current_state == 3:  # Disputed
            print(f"  ⚠️ Consumer 发起了争议")
            return {"phase": "disputed"}

        elif current_state == 4:  # Refunded
            print(f"  ↩️ Consumer 已退款（可能超时）")
            return {"phase": "refunded"}

        elapsed = time.time() - start_time
        print(f"  ... 等待中 ({elapsed:.0f}s)", end="\r")

    return {"phase": "confirm_timeout"}


def handle_dispute(state: ProviderState, client: Web3Client) -> dict:
    """节点 5b：处理争议（展示仲裁提示）"""
    print(f"\n⚖️ [Provider] 当前交易进入争议状态")
    print(f"  💡 提示: 需要人工仲裁或 DAO 投票解决")
    return {"phase": "dispute_handled"}


def handle_refund(state: ProviderState, client: Web3Client) -> dict:
    """节点 5c：处理退款（展示损失）"""
    print(f"\n💸 [Provider] 交易已被退款，本次服务无收入")
    return {"phase": "refund_handled"}


# ── 路由函数 ──

def route_after_register(state: ProviderState) -> str:
    if state.get("error"):
        return END
    return "wait_request"


def route_after_wait(state: ProviderState) -> str:
    if state.get("error"):
        return END
    return "execute_service"


def route_after_deliver(state: ProviderState) -> str:
    if state.get("error"):
        return END
    return "wait_confirmation"


def route_after_confirmation(state: ProviderState) -> str:
    phase = state.get("phase", "")
    if phase == "completed":
        return END
    elif phase == "disputed":
        return "handle_dispute"
    elif phase == "refunded":
        return "handle_refund"
    return END


# ── 构建图 ──

def build_provider_graph(client: Web3Client) -> StateGraph:
    """构建 Provider Agent 的 LangGraph 状态图"""

    workflow = StateGraph(ProviderState)

    # 添加节点
    workflow.add_node("register_service", lambda s: register_service(s, client))
    workflow.add_node("wait_request", lambda s: wait_request(s, client))
    workflow.add_node("execute_service", lambda s: execute_service(s, client))
    workflow.add_node("deliver_result", lambda s: deliver_result(s, client))
    workflow.add_node("wait_confirmation", lambda s: wait_confirmation(s, client))
    workflow.add_node("handle_dispute", lambda s: handle_dispute(s, client))
    workflow.add_node("handle_refund", lambda s: handle_refund(s, client))

    # 设置入口
    workflow.set_entry_point("register_service")

    # 条件边
    workflow.add_conditional_edges("register_service", route_after_register)
    workflow.add_conditional_edges("wait_request", route_after_wait)

    # 固定边
    workflow.add_edge("execute_service", "deliver_result")
    workflow.add_conditional_edges("deliver_result", route_after_deliver)
    workflow.add_conditional_edges("wait_confirmation", route_after_confirmation)

    workflow.add_edge("handle_dispute", END)
    workflow.add_edge("handle_refund", END)

    return workflow.compile()


# ── 便捷调用 ──

def run_provider(
    client: Web3Client,
    service_name: str = SERVICE_NAME,
    service_description: str = "查询美股上市公司的财报核心指标",
    price_eth: float = SERVICE_PRICE_ETH,
) -> dict:
    """运行 Provider Agent 的完整流程"""
    graph = build_provider_graph(client)

    initial_state: ProviderState = {
        "service_name": service_name,
        "service_description": service_description,
        "price_eth": price_eth,
        "current_request_id": 0,
        "current_consumer": "",
        "current_amount": 0,
        "current_service_id": 0,
        "query_result": "",
        "data_hash": "",
        "registered": False,
        "delivered": False,
        "confirmed": False,
        "error": "",
        "phase": "start",
    }

    final_state = graph.invoke(initial_state)
    return final_state
