"""
Agent 数据模型 & 注册中心
========================
管理所有 Agent 的注册、登录、选择、状态查询。
代替中心化数据库，数据存储在 session_state 中。
"""

from dataclasses import dataclass, field
from typing import Optional
from eth_account import Account


# ── Agent 角色 ──
ROLE_PROVIDER = "provider"   # 卖方/服务提供方
ROLE_CONSUMER = "consumer"   # 买方/服务消费方


@dataclass
class Agent:
    """
    一个 AI Agent 在支付系统中的身份。
    """
    id: str                     # 唯一 ID (UUID 或序号)
    name: str                   # 显示名称, 如 "Alice"
    role: str                   # provider / consumer
    private_key: str            # 以太坊私钥
    service_name: str = ""      # 提供的服务名称 (Provider 用)
    service_description: str = ""  # 服务描述
    price_eth: float = 0.0      # 服务价格 (Provider 用)
    description: str = ""       # 自我介绍
    avatar: str = "🤖"          # emoji 头像

    @property
    def address(self) -> str:
        return Account.from_key(self.private_key).address

    @property
    def address_short(self) -> str:
        addr = self.address
        return f"{addr[:6]}...{addr[-4:]}"


# ── 预置的默认 Agent 们 ──
DEFAULT_AGENTS = [
    Agent(
        id="alice",
        name="Alice",
        role=ROLE_PROVIDER,
        private_key="0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
        service_name="financial_report_query",
        service_description="查询美股上市公司的财报核心指标 (EPS/PE/营收等)",
        price_eth=0.01,
        description="金融数据 API Agent，提供美股上市公司财报查询服务。数据来源实时更新。",
        avatar="🤖",
    ),
    Agent(
        id="bob",
        name="Bob",
        role=ROLE_CONSUMER,
        private_key="0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
        description="股票分析 Agent，需要定期查询财报数据进行基本面分析。",
        avatar="🧑‍💼",
    ),
    Agent(
        id="charlie",
        name="Charlie",
        role=ROLE_CONSUMER,
        private_key="0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba",
        description="量化交易 Agent，实时监控市场数据并触发交易策略。",
        avatar="📊",
    ),
    Agent(
        id="diana",
        name="Diana",
        role=ROLE_PROVIDER,
        private_key="0x92db14e403b83dfe3df233f83dfa3a0d709006705eccd8f3cd79f7a0a0a0a0a0",
        service_name="sentiment_analysis",
        service_description="新闻情绪分析，提取正面/负面/中性评分",
        price_eth=0.008,
        description="自然语言处理 Agent，分析新闻 & 社交媒体情绪指标。",
        avatar="🧠",
    ),
    Agent(
        id="eve",
        name="Eve",
        role=ROLE_PROVIDER,
        private_key="0x5fb3df3df23f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a59c6995e",
        service_name="technical_analysis",
        service_description="技术指标计算 (MA/RSI/MACD/Bollinger)",
        price_eth=0.015,
        description="技术分析 Agent，提供 K 线指标计算和图表数据。",
        avatar="📈",
    ),
]


class AgentRegistry:
    """
    Agent 注册中心。
    管理所有已注册 Agent (Provider + Consumer)，
    支持注册、查询、选择当前活跃的 Agent。
    """

    def __init__(self):
        self._agents: dict[str, Agent] = {}
        self._selected_provider_id: str = ""
        self._selected_consumer_id: str = ""

        # 加载默认 Agent
        for agent in DEFAULT_AGENTS:
            self._agents[agent.id] = agent

        # 默认选中
        self._selected_provider_id = "alice"
        self._selected_consumer_id = "bob"

    # ── 查询方法 ──

    def get_all(self) -> list[Agent]:
        """获取所有已注册 Agent"""
        return list(self._agents.values())

    def get_by_role(self, role: str) -> list[Agent]:
        """按角色过滤"""
        return [a for a in self._agents.values() if a.role == role]

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def get_providers(self) -> list[Agent]:
        return self.get_by_role(ROLE_PROVIDER)

    def get_consumers(self) -> list[Agent]:
        return self.get_by_role(ROLE_CONSUMER)

    def count(self) -> int:
        return len(self._agents)

    # ── 注册 / 注销 ──

    def register(self, agent: Agent):
        """注册一个新 Agent (若已存在则覆盖)"""
        self._agents[agent.id] = agent

    def unregister(self, agent_id: str) -> bool:
        """注销一个 Agent"""
        if agent_id in self._agents:
            del self._agents[agent_id]
            # 如果被选中的 Agent 被删除，重置
            if self._selected_provider_id == agent_id:
                self._selected_provider_id = ""
            if self._selected_consumer_id == agent_id:
                self._selected_consumer_id = ""
            return True
        return False

    # ── 选择当前交易双方 ──

    @property
    def selected_provider(self) -> Agent | None:
        return self._agents.get(self._selected_provider_id)

    @property
    def selected_consumer(self) -> Agent | None:
        return self._agents.get(self._selected_consumer_id)

    def select_provider(self, agent_id: str) -> bool:
        """设置当前活跃的 Provider"""
        if agent_id in self._agents and self._agents[agent_id].role == ROLE_PROVIDER:
            self._selected_provider_id = agent_id
            return True
        return False

    def select_consumer(self, agent_id: str) -> bool:
        """设置当前活跃的 Consumer"""
        if agent_id in self._agents and self._agents[agent_id].role == ROLE_CONSUMER:
            self._selected_consumer_id = agent_id
            return True
        return False

    def selected_service_name(self) -> str:
        """当前选中的 Provider 提供的服务名称"""
        p = self.selected_provider
        return p.service_name if p else ""

    def selected_service_price(self) -> float:
        p = self.selected_provider
        return p.price_eth if p else 0.01
