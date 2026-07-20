"""
AgentPay 全局配置
===============
使用前请根据你的本地环境修改以下配置。
"""

import os

# ── 链配置 ──
# Hardhat 本地测试链默认 RPC
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
CHAIN_ID = int(os.getenv("CHAIN_ID", "31337"))  # Hardhat 默认 chainId

# ── 合约地址（部署到本地链后填入）──
# 运行 scripts/deploy.js 后会输出地址，复制到这里
SERVICE_REGISTRY_ADDRESS = os.getenv(
    "SERVICE_REGISTRY_ADDRESS", "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9"
)
ESCROW_PAYMENT_ADDRESS = os.getenv(
    "ESCROW_PAYMENT_ADDRESS", "0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9"
)
REPUTATION_ADDRESS = os.getenv(
    "REPUTATION_ADDRESS", "0x5FC8d32690cc91D4c39d9d3abcBD16989F875707"
)

# ── Agent 钱包私钥（Hardhat 测试账户）──
# Hardhat node 启动时会打印 20 个测试账户
# 账户 #0 (默认 deployer)：0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
# 账户 #1 (Alice/Provider)：0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
# 账户 #2 (Bob/Consumer)：0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a
ALICE_PRIVATE_KEY = os.getenv(
    "ALICE_PRIVATE_KEY",
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
)
BOB_PRIVATE_KEY = os.getenv(
    "BOB_PRIVATE_KEY",
    "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
)

# ── 服务配置 ──
SERVICE_NAME = "financial_report_query"
SERVICE_PRICE_ETH = 0.01  # ETH
# 等待确认的超时时间（秒）
TIMEOUT_SECONDS = 60
# 轮询间隔（秒）
POLL_INTERVAL = 2
