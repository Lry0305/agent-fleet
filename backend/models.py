"""
数据模型 — 多认证方式
=====================
Agent 支持三种登录方式:
  - api_key:      Web Agent，用 ap_sk_xxx
  - feishu_app:   飞书机器人，用 App ID + App Secret
  - openclaw_bot: OpenClaw 机器人，用 Bot Token

AgentPay = 支付结算层，不绑定 Agent 的具体技能。
每个 Agent 导入 SDK 即可获得支付能力。
"""

import uuid, secrets, hashlib
from datetime import datetime
from sqlalchemy import Column, String, Float, Text, DateTime, Boolean, Integer, Enum as SAEnum
from enum import Enum

from backend.database import Base
from backend.config import API_KEY_PREFIX


# ── 枚举 ──

class AgentRole(str, Enum):
    PROVIDER = "provider"
    CONSUMER = "consumer"


class AuthMethod(str, Enum):
    API_KEY = "api_key"          # AgentPay 自生成 API Key
    FEISHU_APP = "feishu_app"    # 飞书应用 App ID
    OPENCLAW_BOT = "openclaw_bot" # OpenClaw Bot Token


class TransactionStatus(str, Enum):
    PENDING = "pending"
    FUND_LOCKED = "fund_locked"
    DELIVERED = "delivered"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    REFUNDED = "refunded"
    FAILED = "failed"


# ── Agent 模型 ──

class AgentModel(Base):
    __tablename__ = "agents"

    id = Column(String(64), primary_key=True, default=lambda: f"ag_{uuid.uuid4().hex[:12]}")
    name = Column(String(128), nullable=False)
    role = Column(SAEnum(AgentRole), nullable=False)

    # ── 认证方式 ──
    auth_method = Column(SAEnum(AuthMethod), nullable=False, default=AuthMethod.API_KEY)
    # api_key 方式
    api_key = Column(String(64), unique=True, nullable=True, index=True)
    secret_hash = Column(String(256), nullable=True)                       # API Key 的 SHA256
    # feishu_app / openclaw_bot 方式
    app_id = Column(String(128), nullable=True, index=True)                # 飞书 App ID 或 Bot 标识
    app_secret_hash = Column(String(256), nullable=True)                   # App Secret 的 SHA256

    # ── Provider 服务信息 ──
    service_name = Column(String(256), default="")
    service_description = Column(Text, default="")
    service_endpoint = Column(String(512), default="")
    price_per_call = Column(Float, default=0.0)
    is_service_active = Column(Boolean, default=True)

    # ── 钱包 ──
    eth_address = Column(String(42), default="")
    encrypted_private_key = Column(Text, default="")

    # ── 余额 ──
    credit_balance = Column(Float, default=0.0)
    total_earned = Column(Float, default=0.0)
    total_spent = Column(Float, default=0.0)

    # ── 元数据 ──
    description = Column(Text, default="")
    avatar = Column(String(8), default="🤖")
    platform = Column(String(64), default="web")
    metadata_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def generate_api_key() -> str:
        return f"{API_KEY_PREFIX}{secrets.token_hex(16)}"

    @staticmethod
    def hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()


# ── 交易模型 ──

class TransactionModel(Base):
    __tablename__ = "transactions"

    id = Column(String(64), primary_key=True, default=lambda: f"tx_{uuid.uuid4().hex[:12]}")
    consumer_id = Column(String(64), nullable=False)
    provider_id = Column(String(64), nullable=False)
    service_name = Column(String(256), default="")
    amount = Column(Float, nullable=False)
    status = Column(SAEnum(TransactionStatus), default=TransactionStatus.PENDING)
    chain_request_id = Column(Integer, default=0)
    chain_tx_hash = Column(String(66), default="")
    data_hash = Column(String(66), default="")
    error_message = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── 服务模型 ──

class ServiceModel(Base):
    __tablename__ = "services"

    id = Column(String(64), primary_key=True, default=lambda: f"sv_{uuid.uuid4().hex[:12]}")
    provider_id = Column(String(64), nullable=False, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, default="")
    endpoint = Column(String(512), default="")
    price = Column(Float, default=0.0)
    is_active = Column(Boolean, default=True)
    chain_service_id = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── 声誉系统 ──

class ReputationModel(Base):
    __tablename__ = "reputation"

    id = Column(String(64), primary_key=True, default=lambda: f"rt_{uuid.uuid4().hex[:12]}")
    provider_id = Column(String(64), nullable=False, index=True)
    consumer_id = Column(String(64), nullable=False)
    transaction_id = Column(String(64), nullable=False, unique=True)
    score = Column(Integer, nullable=False)  # 1-5
    comment = Column(Text, default="")
    chain_tx_hash = Column(String(66), default="")

    created_at = Column(DateTime, default=datetime.utcnow)


# ── OpenClaw 机器人 ──

class OpenClawBot(Base):
    __tablename__ = "openclaw_bots"

    id = Column(String(64), primary_key=True, default=lambda: f"oc_{uuid.uuid4().hex[:12]}")
    agent_id = Column(String(64), nullable=False, index=True)
    bot_token = Column(String(256), nullable=False)
    platform = Column(String(64), default="openclaw")
    webhook_url = Column(String(512), default="")
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
