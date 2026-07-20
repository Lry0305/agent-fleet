"""
AgentPay 后端配置
"""

import os

# ── 数据库 ──
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./backend/agentpay.db")

# ── API ──
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8765"))

# ── 区块链 ──
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
CHAIN_ID = int(os.getenv("CHAIN_ID", "31337"))

# ── 合约地址 ──
SERVICE_REGISTRY = os.getenv("SERVICE_REGISTRY_ADDRESS", "")
ESCROW_PAYMENT = os.getenv("ESCROW_PAYMENT_ADDRESS", "")
REPUTATION = os.getenv("REPUTATION_ADDRESS", "")

# ── OpenClaw ──
OPENCLAW_WEBHOOK_SECRET = os.getenv("OPENCLAW_WEBHOOK_SECRET", "agentpay-webhook-secret-change-me")
OPENCLAW_BOT_TOKEN = os.getenv("OPENCLAW_BOT_TOKEN", "")

# ── 计费 ──
DEFAULT_CREDITS = 100.0       # 新注册送 100 credits
CREDIT_TO_ETH_RATE = 0.01     # 1 credit = 0.01 ETH

# ── Agent API Key 前缀 ──
API_KEY_PREFIX = "ap_sk_"
