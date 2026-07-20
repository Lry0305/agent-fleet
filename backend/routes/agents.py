"""
Agent 注册/登录 — 多认证方式
============================
POST /api/agents/register  支持 api_key / feishu_app / openclaw_bot
POST /api/agents/login     支持三种方式登录
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from backend.database import get_db
from backend.models import AgentModel, AgentRole, AuthMethod
from backend.auth import verify_agent
from backend.config import DEFAULT_CREDITS

router = APIRouter(prefix="/api/agents", tags=["agents"])

from pydantic import BaseModel, Field


class AgentRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    role: str = Field(..., pattern="^(provider|consumer)$")
    auth_method: str = Field(default="api_key", pattern="^(api_key|feishu_app|openclaw_bot)$")

    # feishu_app / openclaw_bot 用
    app_id: Optional[str] = Field(default="")
    app_secret: Optional[str] = Field(default="")

    # Provider 服务信息
    service_name: Optional[str] = Field(default="")
    service_description: Optional[str] = Field(default="")
    price_per_call: Optional[float] = Field(default=0.0)

    # 元数据
    description: Optional[str] = Field(default="")
    avatar: Optional[str] = Field(default="🤖")
    platform: Optional[str] = Field(default="web")


class AgentLoginRequest(BaseModel):
    auth_method: str = Field(default="api_key", pattern="^(api_key|feishu_app|openclaw_bot)$")
    api_key: Optional[str] = Field(default="")        # api_key 方式
    app_id: Optional[str] = Field(default="")         # feishu_app 方式
    app_secret: Optional[str] = Field(default="")     # feishu_app 方式
    bot_token: Optional[str] = Field(default="")      # openclaw_bot 方式


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    avatar: Optional[str] = None
    service_name: Optional[str] = None
    service_description: Optional[str] = None
    service_endpoint: Optional[str] = None
    price_per_call: Optional[float] = None
    is_service_active: Optional[bool] = None


def agent_to_public(agent: AgentModel) -> dict:
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "role": agent.role.value if isinstance(agent.role, AgentRole) else agent.role,
        "auth_method": agent.auth_method.value if isinstance(agent.auth_method, AuthMethod) else agent.auth_method,
        "description": agent.description,
        "avatar": agent.avatar,
        "service_name": agent.service_name,
        "service_description": agent.service_description,
        "price_per_call": agent.price_per_call,
        "is_service_active": agent.is_service_active,
        "eth_address": agent.eth_address,
        "platform": agent.platform,
        "total_earned": agent.total_earned,
        "total_spent": agent.total_spent,
        "created_at": agent.created_at.isoformat() if agent.created_at else "",
    }


# ═══════════════════ 注册 ═══════════════════

@router.post("/register")
def register_agent(req: AgentRegisterRequest, db: Session = Depends(get_db)):
    """
    注册新 Agent。支持三种认证方式:
      - api_key:      自动生成 ap_sk_xxx
      - feishu_app:   需提供 app_id + app_secret
      - openclaw_bot: 需提供 bot_token (作为 app_id)
    """
    auth_method = AuthMethod(req.auth_method)
    api_key = None
    secret_hash = None
    app_id = None
    app_secret_hash = None

    if auth_method == AuthMethod.API_KEY:
        api_key = AgentModel.generate_api_key()
        secret_hash = AgentModel.hash_secret(api_key)
    elif auth_method == AuthMethod.FEISHU_APP:
        if not req.app_id or not req.app_secret:
            raise HTTPException(status_code=400, detail="飞书认证需要提供 app_id 和 app_secret")
        app_id = req.app_id
        app_secret_hash = AgentModel.hash_secret(req.app_secret)
        # 也生成 API Key 备用
        api_key = AgentModel.generate_api_key()
        secret_hash = AgentModel.hash_secret(api_key)
    elif auth_method == AuthMethod.OPENCLAW_BOT:
        if not req.app_id:
            raise HTTPException(status_code=400, detail="OpenClaw 认证需要提供 bot_token (填入 app_id)")
        app_id = req.app_id
        # bot_token 就是认证凭证，直接哈希存储
        app_secret_hash = AgentModel.hash_secret(req.app_secret) if req.app_secret else AgentModel.hash_secret(req.app_id)
        api_key = AgentModel.generate_api_key()
        secret_hash = AgentModel.hash_secret(api_key)

    # 自动生成 ETH 钱包 (链上支付用)
    from eth_account import Account
    wallet = Account.create()
    eth_address = wallet.address
    encrypted_private_key = wallet.key.hex()  # demo 简化: 实际生产应 KMS 加密

    # 给 Consumer 钱包预充一些 ETH (硬编码的 hardhat 账户有 10000 ETH)
    if req.role == "consumer":
        try:
            from backend.chain import _get_w3, chain_available
            if chain_available():
                w3 = _get_w3()
                # 用 hardhat 默认账户 (账户 0, 私钥公开) 给新 Agent 转 ETH
                funder_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
                funder = Account.from_key(funder_key)
                amount_eth = DEFAULT_CREDITS * 0.01  # 100 credits = 1 ETH
                nonce = w3.eth.get_transaction_count(funder.address)
                tx = {
                    "from": funder.address,
                    "to": eth_address,
                    "value": w3.to_wei(amount_eth, "ether"),
                    "gas": 21000,
                    "gasPrice": w3.eth.gas_price,
                    "nonce": nonce,
                    "chainId": 31337,
                }
                signed = w3.eth.account.sign_transaction(tx, funder_key)
                w3.eth.send_raw_transaction(signed.raw_transaction)
                w3.eth.wait_for_transaction_receipt(signed.hash)
                print(f"[Register] ✅ Consumer 钱包预充 {amount_eth} ETH: {eth_address}")
        except Exception as e:
            print(f"[Register] ⚠️ 钱包预充失败 (链上交互跳过): {e}")

    agent = AgentModel(
        name=req.name,
        role=AgentRole.PROVIDER if req.role == "provider" else AgentRole.CONSUMER,
        auth_method=auth_method,
        api_key=api_key,
        secret_hash=secret_hash,
        app_id=app_id,
        app_secret_hash=app_secret_hash,
        description=req.description or "",
        avatar=req.avatar or "🤖",
        platform=req.platform or "web",
        total_earned=DEFAULT_CREDITS,        # 初始 Faucet: total_earned - total_spent = 余额
        credit_balance=DEFAULT_CREDITS,       # 兼容旧字段
        service_name=req.service_name or "",
        service_description=req.service_description or "",
        price_per_call=req.price_per_call or 0.0,
        eth_address=eth_address,
        encrypted_private_key=encrypted_private_key,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    result = {
        "agent_id": agent.id,
        "name": agent.name,
        "role": agent.role.value,
        "auth_method": auth_method.value,
        "credit_balance": agent.total_earned - agent.total_spent,
        "eth_address": eth_address,
    }
    if api_key:
        result["api_key"] = api_key
    return result


# ═══════════════════ 登录 ═══════════════════

@router.post("/login")
def login_agent(req: AgentLoginRequest, db: Session = Depends(get_db)):
    """
    统一登录入口。
    api_key 方式:   {"auth_method":"api_key","api_key":"ap_sk_xxx"}
    feishu_app 方式: {"auth_method":"feishu_app","app_id":"cli_xxx","app_secret":"xxx"}
    openclaw_bot:   {"auth_method":"openclaw_bot","bot_token":"xxx"}
    """
    method = AuthMethod(req.auth_method)
    agent = None

    if method == AuthMethod.API_KEY:
        if not req.api_key:
            raise HTTPException(status_code=400, detail="需要 api_key")
        key_hash = AgentModel.hash_secret(req.api_key)
        agent = db.query(AgentModel).filter(AgentModel.secret_hash == key_hash).first()

    elif method == AuthMethod.FEISHU_APP:
        if not req.app_id or not req.app_secret:
            raise HTTPException(status_code=400, detail="飞书登录需要 app_id + app_secret")
        sh = AgentModel.hash_secret(req.app_secret)
        agent = db.query(AgentModel).filter(
            AgentModel.auth_method == AuthMethod.FEISHU_APP,
            AgentModel.app_id == req.app_id,
            AgentModel.app_secret_hash == sh,
        ).first()

    elif method == AuthMethod.OPENCLAW_BOT:
        if not req.bot_token:
            raise HTTPException(status_code=400, detail="需要 bot_token")
        sh = AgentModel.hash_secret(req.bot_token)
        agent = db.query(AgentModel).filter(
            AgentModel.auth_method == AuthMethod.OPENCLAW_BOT,
            AgentModel.app_secret_hash == sh,
        ).first()

    if not agent:
        raise HTTPException(status_code=401, detail="认证失败")

    return {
        "agent_id": agent.id,
        "name": agent.name,
        "role": agent.role.value,
        "auth_method": agent.auth_method.value,
        "api_key": agent.api_key,  # 返回 api_key 供 SDK 使用
        "credit_balance": agent.total_earned - agent.total_spent,
        "total_earned": agent.total_earned,
        "total_spent": agent.total_spent,
        "avatar": agent.avatar,
        "message": f"欢迎回来，{agent.name}！",
    }


# ═══════════════════ 查询/更新 ═══════════════════

@router.get("/me")
def get_my_profile(agent: AgentModel = Depends(verify_agent)):
    return {
        **agent_to_public(agent),
        "credit_balance": agent.total_earned - agent.total_spent,
        "total_earned": agent.total_earned,
        "total_spent": agent.total_spent,
        "service_endpoint": agent.service_endpoint,
        "platform": agent.platform,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else "",
    }


@router.put("/me")
def update_my_profile(
    req: AgentUpdateRequest,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    for field, value in req.model_dump(exclude_none=True).items():
        setattr(agent, field, value)
    agent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(agent)
    return {"message": "更新成功", **agent_to_public(agent)}


@router.get("/all")
def list_all_agents(
    role: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(AgentModel)
    if role == "provider":
        q = q.filter(AgentModel.role == AgentRole.PROVIDER)
    elif role == "consumer":
        q = q.filter(AgentModel.role == AgentRole.CONSUMER)
    agents = q.order_by(AgentModel.created_at.desc()).all()
    return {"total": len(agents), "agents": [agent_to_public(a) for a in agents]}


@router.get("/{agent_id}")
def get_agent(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return agent_to_public(agent)
