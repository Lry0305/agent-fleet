"""
飞书机器人 Webhook 路由
======================
接收飞书群 @消息 → 自动解析意图 → AgentFleet 支付 → 回复结果。

不需要 OpenClaw，直接对接飞书开放平台事件回调。

流程:
  用户在飞书群 @每日投资早报 "腾讯怎么样？"
       │
       ▼
  飞书服务器 ── HTTPS POST ──► POST /api/feishu/webhook
       │                         │
       │                         ├─ 1. URL Verification (challenge)
       │                         ├─ 2. 解析消息文本，提取股票代码
       │                         ├─ 3. 查 DB → 找 Consumer/Provider
       │                         ├─ 4. 自动 pay → confirm (credits)
       │                         ├─ 5. 飞书 API 回复分析结果
       │                         └─ 6. 返回 200 OK
       │
       ◄── 回复消息 ────────────── 飞书群显示分析结果

飞书事件回调配置:
  请求网址 URL: https://你的域名.ngrok.io/api/feishu/webhook
  事件: im.message.receive_v1 (接收消息)
"""

import json, re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import AgentModel, AgentRole, AuthMethod
from backend.feishu_client import FeishuClient

router = APIRouter(prefix="/api/feishu", tags=["feishu"])

# ── 预配置的飞书机器人凭据（从环境变量读取，不硬编码） ──
import os as _os
FEISHU_BOTS = {}
_consumer_id = _os.getenv("FEISHU_CONSUMER_APP_ID", "cli_aad1341ec638dbc9")
_consumer_secret = _os.getenv("FEISHU_CONSUMER_APP_SECRET", "")
_provider_id = _os.getenv("FEISHU_PROVIDER_APP_ID", "cli_aad136137db81bda")
_provider_secret = _os.getenv("FEISHU_PROVIDER_APP_SECRET", "")
if _consumer_id:
    FEISHU_BOTS["每日投资早报"] = {
        "app_id": _consumer_id,
        "app_secret": _consumer_secret,
        "role": "consumer",
    }
if _provider_id:
    FEISHU_BOTS["技术面分析大师"] = {
        "app_id": _provider_id,
        "app_secret": _provider_secret,
        "role": "provider",
    }

# ── 股票关键词 → 代码映射 ──
STOCK_KEYWORDS = {
    "腾讯": "0700.HK", "tencent": "0700.HK",
    "阿里": "9988.HK", "alibaba": "9988.HK",
    "苹果": "AAPL", "apple": "AAPL",
    "特斯拉": "TSLA", "tesla": "TSLA",
    "英伟达": "NVDA", "nvidia": "NVDA",
    "谷歌": "GOOGL", "google": "GOOGL",
    "微软": "MSFT", "microsoft": "MSFT",
    "亚马逊": "AMZN", "amazon": "AMZN",
}


def extract_stock(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    从消息文本中提取股票名称和代码。
    Returns: (stock_name, stock_symbol) 或 (None, None)
    """
    for keyword, symbol in STOCK_KEYWORDS.items():
        if keyword.lower() in text.lower():
            # 找到反向映射的显示名
            name_map = {
                "0700.HK": "腾讯", "9988.HK": "阿里",
                "AAPL": "苹果", "TSLA": "特斯拉",
                "NVDA": "英伟达", "GOOGL": "谷歌",
                "MSFT": "微软", "AMZN": "亚马逊",
            }
            stock_name = name_map.get(symbol, keyword)
            return stock_name, symbol
    return None, None


def detect_complexity(text: str) -> tuple[str, float]:
    """
    根据消息文本检测任务复杂度，返回 (tier_name, price_multiplier)。

    简单查询(2x): 怎么样/什么/多少/是/吗
    标准分析(5x): 分析/走势/K线/技术/指标
    深度研究(10x): 深度/报告/详细/全面/对比/预测
    """
    simple_kw = ["怎么样", "什么", "多少", "是", "吗", "查询", "在哪"]
    standard_kw = ["分析", "走势", "K线", "技术", "指标", "macd", "rsi", "布林"]
    complex_kw = ["深度", "报告", "详细", "全面", "对比", "预测", "展望", "建议"]

    for kw in complex_kw:
        if kw.lower() in text.lower():
            return ("深度研究", 2.0)
    for kw in standard_kw:
        if kw.lower() in text.lower():
            return ("标准分析", 1.0)
    for kw in simple_kw:
        if kw.lower() in text.lower():
            return ("简单查询", 0.4)
    return ("标准分析", 1.0)


def calculate_price(base_price: float, complexity_multiplier: float) -> int:
    """根据 base 价格和复杂度倍率计算最终价格 (整数 credits)"""
    return max(1, round(base_price * complexity_multiplier))


def mock_technical_analysis(symbol: str) -> dict:
    """
    模拟技术面分析结果。
    生产环境由 Provider 的 CUA-driver 执行。
    """
    analyses = {
        "0700.HK": {"macd": "金叉", "rsi": 62, "bollinger": "上轨突破", "volume": "放量", "verdict": "短线看多 📈"},
        "9988.HK": {"macd": "死叉", "rsi": 38, "bollinger": "下轨支撑", "volume": "缩量", "verdict": "观望 ⏸️"},
        "AAPL":   {"macd": "金叉", "rsi": 55, "bollinger": "中轨企稳", "volume": "温和", "verdict": "震荡偏多 📊"},
        "TSLA":   {"macd": "金叉", "rsi": 70, "bollinger": "上轨附近", "volume": "放量", "verdict": "强势看多 🚀"},
    }
    base = analyses.get(symbol, {})
    if not base:
        base = {
            "macd": random.choice(["金叉", "死叉", "粘合"]),
            "rsi": random.randint(30, 70),
            "bollinger": random.choice(["上轨突破", "中轨企稳", "下轨支撑"]),
            "volume": random.choice(["放量", "缩量", "温和"]),
            "verdict": random.choice(["看多 📈", "看空 📉", "观望 ⏸️"]),
        }
    return base


def self_check_status(db=None) -> str:
    """Provider 状态自检"""
    if not db:
        return "技术面分析大师 — 运行中\n使用 `agentfleet-skill serve` 启动本地服务"
    provider = db.query(AgentModel).filter(
        AgentModel.service_name == "stock_analysis"
    ).first() if db else None
    if provider:
        bal = provider.total_earned - provider.total_spent
        return (
            f"当前状态:\n"
            f"  平台余额: {bal} credits\n"
            f"  总收入: {provider.total_earned} credits\n"
            f"  ETH钱包: {provider.eth_address[:10]}...\n"
            f"  基准价格: {provider.price_per_call} credits/次"
        )
    return "技术面分析大师 — 已注册"

# ═══════════════════════════════════════════════════════════
#  Webhook 端点
# ═══════════════════════════════════════════════════════════

@router.post("/webhook")
def feishu_webhook(payload: dict, db: Session = Depends(get_db)):
    """
    飞书事件回调入口。
    支持:
      - URL Verification (验证回调地址)
      - im.message.receive_v1 (接收消息)

    直接操作 DB，不自调 API，避免 self-recursion 超时。
    """

    # ── 1. URL 验证 ──
    challenge = FeishuClient.verify_challenge(payload)
    if challenge:
        return {"challenge": challenge}

    # ── 2. 消息事件 ──
    header = payload.get("header", {})
    event_type = header.get("event_type", "")
    if event_type != "im.message.receive_v1":
        return {"status": "ignored", "event_type": event_type}

    event = payload.get("event", {})
    message = event.get("message", {})
    sender = event.get("sender", {})

    message_id = message.get("message_id", "")
    chat_type = message.get("chat_type", "")  # "group" or "p2p"
    content_str = message.get("content", "{}")

    # 解析消息文本
    text = FeishuClient.parse_text_content(content_str)
    if not text:
        return {"status": "empty_message"}

    print(f"\n📨 飞书消息: [{text}] (from {sender.get('sender_id',{}).get('open_id','')})")

    # ── 3. 识别是哪个机器人收到消息 ──
    bot_app_id = header.get("app_id", "")
    # 判断是 Consumer 还是 Provider
    is_consumer_bot = (bot_app_id == "cli_aad1341ec638dbc9")
    is_provider_bot = (bot_app_id == "cli_aad136137db81bda")

    # 获取对应飞书客户端，用于回复
    bot_client = None
    bot_name = ""
    for name, info in FEISHU_BOTS.items():
        if info["app_id"] == bot_app_id:
            bot_client = FeishuClient(info["app_id"], info["app_secret"])
            bot_name = name
            break

    if not bot_client:
        return {"status": "unknown_bot"}

    # ── 4. Provider 机器人专用路径 ──
    if is_provider_bot:
        help_text = (
            f"你好，我是[{bot_name}] 🤖\n\n"
            "我是 AgentFleet 上的技术分析 Provider。\n\n"
            "当前状态：\n"
            f"  • 基准价格: 5 credits/次\n"
            "  • 价格按复杂度阶梯: 简单(2cr) / 标准(5cr) / 深度(10cr)\n"
            f"  • 知识库: 腾讯/阿里/苹果/特斯拉/英伟达/谷歌/微软/亚马逊\n\n"
            "如何使用：\n"
            "发消息给「每日投资早报」说「腾讯怎么样」-> "
            "它自动支付 + 我执行分析 + 链上结算"
        )

        # 处理特殊命令
        text_lower = text.lower()
        if "安装" in text or "装" in text or "代码" in text:
            help_text = (
                "📦 本地运行方式：\n\n"
                "cd agentfleet-skill\n"
                "pip install -e .\n"
                "agentfleet-skill onboard --name 技术面分析大师 \\\n"
                "  --auth feishu_app \\\n"
                "  --app-id cli_aad136137db81bda \\\n"
                "  --app-secret <你的飞书App Secret>\n"
                "agentfleet-skill serve\n\n"
                "启动 serve 后我会自动监听订单并执行分析。"
            )
        elif "状态" in text or "status" in text_lower:
            help_text = self_check_status(db)
        elif "定价" in text or "价格" in text or "多少钱" in text:
            help_text = (
                "阶梯定价：\n"
                "  简单查询(怎么样/多少)  → x0.4 倍\n"
                "  标准分析(分析/K线/MACD) → x1.0 倍\n"
                "  深度研究(深度/报告/对比) → x2.0 倍\n\n"
                "基准价格 5 credits，例如「腾讯分析」= 5cr"
            )

        reply_result = bot_client.reply_text(message_id, help_text)
        print(f"  Provider 回复: {reply_result.get('code', '')}")
        return {"status": "provider_replied"}

    # ── 5. Consumer 机器人路径 (原流程) ──
    stock_name, stock_symbol = extract_stock(text)
    if not stock_name:
        # 无匹配股票，回复帮助
        help_text = (
            f"我是 [{bot_name}] 🤖\n"
            f"我是 AgentFleet 消费方机器人。\n"
            f"发消息如「腾讯怎么样」「苹果分析」等\n"
            f"我会自动支付 2-10 credits 调技术面分析大师出报告\n"
            f"支持的股票: {', '.join(sorted(set(k for k, v in STOCK_KEYWORDS.items() if not k.isupper())))}"
        )
        bot_client.reply_text(message_id, help_text)
        return {"status": "help_replied"}

    # ── 6. 查 DB → 找 Consumer 和 Provider ──
    consumer = db.query(AgentModel).filter(
        AgentModel.auth_method == AuthMethod.FEISHU_APP,
        AgentModel.app_id == "cli_aad1341ec638dbc9",
    ).first()

    provider = db.query(AgentModel).filter(
        AgentModel.role == AgentRole.PROVIDER,
        AgentModel.service_name == "stock_analysis",
    ).first()

    if not consumer or not provider:
        print("❌ Consumer 或 Provider 未注册")
        return {"status": "agents_not_found"}

    # ── 7. 执行 AgentFleet 支付流程 (动态定价) ──
    from backend.models import TransactionModel, TransactionStatus
    from backend.chain import chain_available, lock_funds_on_chain, release_funds_on_chain

    try:
        tier_name, multiplier = detect_complexity(text)
        base_price = provider.price_per_call or 5
        amount = calculate_price(base_price, multiplier)

        consumer_balance = consumer.total_earned - consumer.total_spent
        if consumer_balance < amount:
            raise RuntimeError(f"Consumer [{consumer.name}] 余额不足: 需要 {amount} credits, 当前 {consumer_balance}")

        consumer.total_spent += amount
        chain_tx_hash = ""
        chain_request_id = 0

        if chain_available() and consumer.encrypted_private_key and provider.eth_address:
            try:
                chain_info = lock_funds_on_chain(
                    consumer.encrypted_private_key, provider.eth_address, amount,
                )
                if chain_info:
                    chain_tx_hash = chain_info["tx_hash"]
                    chain_request_id = chain_info["request_id"]
                    print(f"  🔗 链上锁定: {chain_tx_hash[:20]}...")
            except Exception as e:
                print(f"  ⚠️ 链上锁定失败: {e}")

        tx1 = TransactionModel(
            consumer_id=consumer.id, provider_id=provider.id,
            service_name="stock_analysis", amount=amount,
            status=TransactionStatus.FUND_LOCKED,
            chain_tx_hash=chain_tx_hash, chain_request_id=chain_request_id,
        )
        db.add(tx1)
        db.flush()
        print(f"  💳 支付 {amount} cr (复杂度: {tier_name}, x{multiplier})")

        analysis = mock_technical_analysis(stock_symbol)
        verdict = analysis.get("verdict", "")

        # Provider 收到付款 (一次性入账)
        provider.total_earned += amount
        tx1.status = TransactionStatus.CONFIRMED
        chain_confirm_hash = ""

        if chain_request_id and consumer.encrypted_private_key:
            try:
                chain_result = release_funds_on_chain(
                    consumer.encrypted_private_key, chain_request_id
                )
                if chain_result:
                    chain_confirm_hash = chain_result["tx_hash"]
                    print(f"  🔗 链上确认: {chain_confirm_hash[:20]}...")
            except Exception as e:
                print(f"  ⚠️ 链上确认失败: {e}")

        db.commit()
        print(f"  ✅ Provider 总额 = {provider.total_earned}")

        # ── 8. 飞书回复 ──
        chain_tag = f"\n🔗 链上: {chain_tx_hash[:14]}..." if chain_tx_hash else ""
        reply_text = (
            f"{stock_name}({stock_symbol}) 技术面分析\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"MACD: {analysis.get('macd', 'N/A')}\n"
            f"RSI:  {analysis.get('rsi', 'N/A')}\n"
            f"布林带: {analysis.get('bollinger', 'N/A')}\n"
            f"成交量: {analysis.get('volume', 'N/A')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"结论: {verdict}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"定价: {tier_name} (x{multiplier}) | {amount} credits ✅"
            f"{chain_tag}"
        )

        reply_result = bot_client.reply_text(message_id, reply_text)
        print(f"  📬 回复 code={reply_result.get('code', '')}")

    except Exception as e:
        print(f"❌ 支付流程失败: {e}")
        import traceback; traceback.print_exc()
        try:
            bot_client.reply_text(message_id, f"⚠️ 分析失败: {str(e)[:100]}")
        except Exception:
            pass

    return {"status": "ok"}
