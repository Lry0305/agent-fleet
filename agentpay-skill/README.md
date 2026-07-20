# AgentPay Skill — 独立子项目

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![CUA-Driver](https://img.shields.io/badge/CUA--Driver-macOS%20自动化-orange)](https://github.com/anthropics/cua-driver)
[![Web3](https://img.shields.io/badge/Web3-链上结算-green)](https://web3py.readthedocs.io)

**Provider Agent 完整方案：CUA-Driver 桌面自动化 + AgentPay SDK 支付 + 链上 ETH 结算。**

这是 AgentPay 的独立子项目。Provider Agent 开发者克隆这个项目，安装依赖，即可拥有：
- 🖥️ 操控 macOS 桌面的能力（浏览器、终端、文件系统）
- 💰 AgentPay 支付集成（注册 → 登录 → 执行计费）
- ⛓️ 链上 ETH 钱包（真正收发 ETH）

---

## 安装

```bash
cd agentpay-skill
pip install -e .            # 安装 agentpay-skill 命令
# 或
pip install -r requirements.txt
```

## 快速开始

### 1. Provider 上线

```bash
# 方式 A: API Key 认证
agentpay-skill onboard --name "StockMaster" --auth api_key

# 方式 B: 飞书 App ID 认证
agentpay-skill onboard --name "FeishuBot" \
  --auth feishu_app \
  --app-id cli_xxx \
  --app-secret xxx \
  --service stock_analysis

# 方式 C: OpenClaw Bot Token 认证
agentpay-skill onboard --name "OCBot" \
  --auth openclaw_bot \
  --app-id bot_token_xxx
```

### 2. 启动服务（监听交易）

```bash
agentpay-skill serve
```

Provider 将持续监听，收到 Consumer 支付后：
1. CUA-Driver 操控 macOS 桌面执行任务
2. AgentPay SDK 自动计费

### 3. Consumer 支付（另一个终端）

```bash
agentpay-skill onboard --name "TraderBot" --auth api_key
agentpay-skill pay --provider ag_xxx --amount 10
```

### 4. 查看状态

```bash
agentpay-skill status    # 余额、交易记录
agentpay-skill wallet    # 链上钱包地址
agentpay-skill info      # 系统信息
agentpay-skill demo-stock --symbol AAPL  # 演示股票分析
```

---

## 工作流程

```
┌─────────────────┐          ┌──────────────┐          ┌─────────────┐
│   Provider      │          │   AgentPay   │          │  Consumer   │
│  (macOS 桌面)    │          │  API + 合约   │          │             │
└────────┬────────┘          └──────┬───────┘          └──────┬──────┘
         │                          │                         │
         │ 1. onboard 注册上线        │                         │
         │ ─────────────────────────►│                         │
         │    生成 ETH 钱包           │                         │
         │    发布服务到市场           │                         │
         │                          │                         │
         │                          │ 2. pay 锁定 ETH          │
         │                          │ ◄────────────────────────│
         │                          │    资金锁入 Escrow 合约   │
         │                          │                         │
         │ 3. 监听到交易              │                         │
         │ ◄─────────────────────────│                         │
         │                          │                         │
         │ 4. CUA-Driver 执行        │                         │
         │ ┌──────────────────┐     │                         │
         │ │ 操控浏览器查数据    │     │                         │
         │ │ 操控终端执行命令    │     │                         │
         │ │ 操控文件系统       │     │                         │
         │ └──────────────────┘     │                         │
         │                          │                         │
         │ 5. SDK 自动计费            │                         │
         │ ─────────────────────────►│                         │
         │                          │                         │
         │                          │ 6. confirm 释放 ETH      │
         │    💰 ETH 到账！           │ ────────────────────────►│
         │ ◄─────────────────────────│                         │
```

---

## 项目结构

```
agentpay-skill/
├── README.md
├── requirements.txt           # 依赖 (cua-driver, web3, click, rich)
├── setup.py                   # pip install -e .
│
├── agentpay_skill/            # 核心包
│   ├── __init__.py
│   ├── cli.py                 # CLI: onboard / serve / pay / status
│   ├── cua_runner.py          # CUA-Driver macOS 桌面自动化
│   ├── wallet.py              # ETH 链上钱包管理
│   └── skills.py              # Skill 注册表 + 内置 Skill
│
├── skills/                    # 独立 Provider 技能模块
│   ├── __init__.py
│   ├── stock_analysis.py      # 股票分析 Provider
│   └── translation.py         # 翻译 Provider
│
└── examples/
    └── onboard_and_serve.py   # 完整流程演示
```

---

## 架构设计

### AgentPay = 支付层，不绑定技能

```
┌──────────────────────────────────────────┐
│  Provider Agent (本项目)                   │
│                                           │
│  ┌─────────────────────────────────┐     │
│  │  你的技能 (独立模块)               │     │
│  │  skills/stock_analysis.py        │     │
│  │  skills/translation.py           │     │
│  │  可以自由扩展任意技能               │     │
│  └─────────────────────────────────┘     │
│                    +                      │
│  ┌─────────────────────────────────┐     │
│  │  AgentPay SDK (支付能力)          │     │
│  │  agents/agentpay_sdk.py          │     │
│  │  注册/登录/支付/链上结算            │     │
│  └─────────────────────────────────┘     │
│                    +                      │
│  ┌─────────────────────────────────┐     │
│  │  CUA-Driver (桌面自动化)          │     │
│  │  agentpay_skill/cua_runner.py    │     │
│  │  浏览器/终端/文件系统操控           │     │
│  └─────────────────────────────────┘     │
│                    +                      │
│  ┌─────────────────────────────────┐     │
│  │  Wallet (链上钱包)                │     │
│  │  agentpay_skill/wallet.py        │     │
│  │  ETH 地址/私钥/收发               │     │
│  └─────────────────────────────────┘     │
└──────────────────────────────────────────┘
```

### CUA-Driver 降级策略

当 CUA-driver 未安装时，自动降级为模拟模式：
- `cua.available` → `False`
- `cua.execute()` 返回模拟数据
- 不影响注册、支付、链上结算流程

---

## 扩展 Skill

在 `skills/` 目录下新建模块：

```python
# skills/my_skill.py
from agentpay_skill.cua_runner import CUARunner

class MyProvider:
    def __init__(self):
        self.cua = CUARunner()

    def execute(self, target: str) -> dict:
        # 你的业务逻辑
        result = self.cua.browser_search(target)
        return {"output": result}

    @staticmethod
    def service_info():
        return {
            "name": "my_service",
            "description": "我的自定义服务",
            "price": 3.0,
            "task_type": "browser_search",
        }
```

然后在 `agentpay_skill/skills.py` 中注册：

```python
from skills.my_skill import MyProvider
MY_SKILL = Skill(name="my_service", ...)
registry.register(MY_SKILL)
```

---

## 前置依赖

- Python >= 3.10
- macOS（CUA-Driver 需要）
- AgentPay 后端运行中（`http://127.0.0.1:8765`）
- 可选：CUA-Driver (`pip install cua-driver`)

---

## License

MIT License
