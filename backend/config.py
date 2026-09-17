"""
AgentFleet 后端配置
"""

import json
import os
from pathlib import Path

# ── 数据库 ──
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./backend/agentfleet.db")

# ── API ──
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8765"))

# ── 区块链 ──
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
CHAIN_ID = int(os.getenv("CHAIN_ID", "31337"))

# ── 合约地址：取值顺序 = 环境变量 → chain_addresses.json（deploy.js 生成） → "" ──
def _load_chain_addresses() -> dict:
    for p in (
        Path(__file__).resolve().parent.parent / "chain_addresses.json",
        Path.cwd() / "chain_addresses.json",
    ):
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
    return {}


_CHAIN_ADDRESSES = _load_chain_addresses()

SERVICE_REGISTRY = os.getenv("SERVICE_REGISTRY_ADDRESS") or _CHAIN_ADDRESSES.get("serviceRegistry", "")
ESCROW_PAYMENT = os.getenv("ESCROW_PAYMENT_ADDRESS") or _CHAIN_ADDRESSES.get("escrowPayment", "")
REPUTATION = os.getenv("REPUTATION_ADDRESS") or _CHAIN_ADDRESSES.get("reputation", "")

# ── 本地演示专用 funder 账户（Hardhat 账户 #0，生产环境切勿使用）──
FUNDER_PRIVATE_KEY = os.getenv(
    "FUNDER_PRIVATE_KEY",
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
)

# ── OpenClaw ──
OPENCLAW_WEBHOOK_SECRET = os.getenv("OPENCLAW_WEBHOOK_SECRET", "agentfleet-webhook-secret-change-me")
OPENCLAW_BOT_TOKEN = os.getenv("OPENCLAW_BOT_TOKEN", "")

# ── 计费 ──
DEFAULT_CREDITS = 100.0       # 新注册送 100 credits
CREDIT_TO_ETH_RATE = 0.01     # 1 credit = 0.01 ETH

# ── 按 token 计价（全平台统一费率，不是 provider 自报价）──
# 选 provider 应该是任务适配导向，不是价格导向：老板/agent 都估不准一个任务要产出多少
# token，与其让 provider 猜着报价、老板猜着填预授权上限，不如价格从一开始就是统一的，
# 谁都不用猜数字。旧版 price_per_call 字段还留着（避免动数据库 schema），但不再被读取用
# 来算锁仓/结算价——真正生效的只有下面这个全局费率。
GLOBAL_RATE_PER_1K = 10.0   # 全平台统一费率：credits / 1000 个交付物 token

# 派单时任务真实会产出多少 token 还不知道，所以先按这个假设的保守输出上限自动算一个
# "预授权锁仓"额（GLOBAL_RATE_PER_1K × DEFAULT_MAX_TOKENS_CAP / 1000），不用老板/agent
# 猜数字；任务交付后拿交付物的真实 token 数结算，多退（锁多了会退差价）少不补
# （provider 少产出不会多收）。
DEFAULT_MAX_TOKENS_CAP = 4000

# ── 充值（老板：支付宝 / 微信 / 银行卡）──
CNY_TO_CREDIT_RATE = 10.0     # 1 元 = 10 credits
PAYMENT_CHANNELS = {
    "alipay": "支付宝",
    "wechat": "微信支付",
    "card": "银行卡",
}

# ── 演示老板账号（一键登录用）──
DEMO_BOSS_PHONE = os.getenv("DEMO_BOSS_PHONE", "13800000000")
DEMO_BOSS_PASSWORD = os.getenv("DEMO_BOSS_PASSWORD", "demo123456")
DEMO_BOSS_NAME = "演示老板"

# ── Agent API Key 前缀 ──
API_KEY_PREFIX = "ap_sk_"
