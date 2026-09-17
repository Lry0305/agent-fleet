"""
Agent 注册/登录 — 多认证方式
============================
POST /api/agents/register  支持 api_key / feishu_app / openclaw_bot
POST /api/agents/login     支持三种方式登录
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from pathlib import Path
from typing import Optional
import json

from backend.database import get_db
from backend.models import AgentModel, AgentRole, AuthMethod
from backend.auth import verify_agent
from backend.config import DEFAULT_CREDITS, FUNDER_PRIVATE_KEY
from backend.chain import _get_w3, chain_available
from eth_account import Account

router = APIRouter(prefix="/api/agents", tags=["agents"])

# agent 自己的 edge 空间根目录：每个 agent 出生即分到 agent_workspaces/<id>/
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent / "agent_workspaces"

from pydantic import BaseModel, Field


class AgentRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    role: str = Field(..., pattern="^(provider|consumer|boss)$")
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


def _fund_wallet(w3, to_address: str, amount_eth: float) -> bool:
    """用本地演示 funder 账户给新钱包转 ETH（Consumer 要锁仓、Provider 要 gas，两种角色都需要）"""
    try:
        funder = Account.from_key(FUNDER_PRIVATE_KEY)
        nonce = w3.eth.get_transaction_count(funder.address)
        tx = {
            "from": funder.address,
            "to": w3.to_checksum_address(to_address),
            "value": w3.to_wei(amount_eth, "ether"),
            "gas": 21000,
            "gasPrice": w3.eth.gas_price,
            "nonce": nonce,
            "chainId": w3.eth.chain_id,
        }
        signed = w3.eth.account.sign_transaction(tx, FUNDER_PRIVATE_KEY)
        w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(signed.hash)
        return True
    except Exception as e:
        print(f"[Register] ⚠️ 钱包充值失败: {e}")
        return False


def agent_to_public(agent: AgentModel) -> dict:
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "role": agent.role.value if isinstance(agent.role, AgentRole) else agent.role,
        "auth_method": agent.auth_method.value if isinstance(agent.auth_method, AuthMethod) else agent.auth_method,
        "description": agent.description,
        "avatar": agent.avatar,
        "avatar_color": getattr(agent, "avatar_color", None) or "#3370ff",
        "service_name": agent.service_name,
        "service_description": agent.service_description,
        "price_per_call": agent.price_per_call,
        "is_service_active": agent.is_service_active,
        "eth_address": agent.eth_address,
        "did": agent.did,
        "platform": agent.platform,
        "total_earned": agent.total_earned,
        "total_spent": agent.total_spent,
        "created_at": agent.created_at.isoformat() if agent.created_at else "",
        "workspace": {
            "mode": getattr(agent, "workspace_mode", "isolated") or "isolated",
            "path": f"agent_workspaces/{agent.id}",
        },
    }


def _build_manifest(agent: AgentModel) -> dict:
    """机器可读能力清单：身份 + speaks（懂的动词）+ skills（能力 + 价格）。

    speaks = 我能听懂（接收）的协议动词集 → 能力协商 / 可扩展的关键：
    中继只投递收件方声明能懂的动词；新 agent 说新动词只需声明，老 agent 优雅忽略。
    """
    from backend.models import PROVIDER_SPEAKS, BOSS_SPEAKS

    skills = []
    if agent.role == AgentRole.PROVIDER and agent.service_name:
        skills.append({
            "name": agent.service_name,
            "description": agent.service_description or "",
            "input_schema": {},
            "output_schema": {},
            "price": agent.price_per_call or 0.0,
            "price_currency": "credits",
        })

    speaks = PROVIDER_SPEAKS if agent.role == AgentRole.PROVIDER else BOSS_SPEAKS
    return {
        "did": agent.did,
        "name": agent.name,
        "role": agent.role.value if isinstance(agent.role, AgentRole) else agent.role,
        "protocol_version": "1.0",
        "speaks": speaks,
        "skills": skills,
        "capabilities": skills,          # 兼容旧字段名
        "endpoint": agent.service_endpoint or "",
        "workspace": {
            "mode": getattr(agent, "workspace_mode", "isolated") or "isolated",
            "path": f"agent_workspaces/{agent.id}",
        },
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
    wallet = Account.create()
    eth_address = wallet.address
    encrypted_private_key = wallet.key.hex()  # demo 简化: 实际生产应 KMS 加密

    # 给新 Agent 钱包预充 ETH（Consumer 要锁仓、Provider 要 gas，两种角色都需要）
    if chain_available():
        try:
            w3 = _get_w3()
            if w3:
                amount_eth = DEFAULT_CREDITS * 0.01  # 100 credits = 1 ETH
                if _fund_wallet(w3, eth_address, amount_eth):
                    print(f"[Register] ✅ {req.role} 钱包预充 {amount_eth} ETH: {eth_address}")
        except Exception as e:
            print(f"[Register] ⚠️ 钱包预充失败 (链上交互跳过): {e}")

    agent = AgentModel(
        name=req.name,
        role=AgentRole(req.role),
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
    # manifest 里的 workspace.path 依赖 agent.id，必须等 commit+refresh 真正拿到 id 之后再生成，
    # 不然自注册出来的 agent 会把 "agent_workspaces/None" 永久存进 metadata_json 里（真实 bug）。
    agent.set_manifest(_build_manifest(agent))
    db.commit()

    result = {
        "agent_id": agent.id,
        "name": agent.name,
        "role": agent.role.value,
        "auth_method": auth_method.value,
        "credit_balance": agent.total_earned - agent.total_spent,
        "eth_address": eth_address,
        "did": agent.did,
        "manifest": agent.get_manifest(),
        # 一次性返回私钥：SDK 持有并用于消息签名（demo 简化；生产由 agent 自己生成、平台只存公钥）
        "private_key": encrypted_private_key,
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
        "eth_address": agent.eth_address,
        "did": agent.did,
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
    if role in ("provider", "consumer", "boss"):
        q = q.filter(AgentModel.role == AgentRole(role))
    agents = q.order_by(AgentModel.created_at.desc()).all()
    return {"total": len(agents), "agents": [agent_to_public(a) for a in agents]}


@router.get("/{agent_id}/manifest")
def get_agent_manifest(agent_id: str, db: Session = Depends(get_db)):
    """机器可读能力清单（discover 原语）。"""
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    m = agent.get_manifest()
    m.setdefault("did", agent.did)
    m.setdefault("name", agent.name)
    return m


@router.put("/me/manifest")
def set_my_manifest(
    manifest: dict,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    agent.set_manifest(manifest)
    agent.updated_at = datetime.utcnow()
    db.commit()
    return agent.get_manifest()


@router.get("/{agent_id}")
def get_agent(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return agent_to_public(agent)


# ═══════════════════ memory 原语：agent 自己的 edge 空间 ═══════════════════
# 协议文档「五层基础设施」里唯一还没实现的一层。脑子在 edge，不进共享真相层，
# 平台不没收：只有 agent 自己的 api_key 能读写自己的记忆，老板也写不了。

class MemoryAppendRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=4000)


def _memory_path(agent_id: str) -> Path:
    return WORKSPACE_ROOT / agent_id / "memory.json"


@router.get("/me/memory")
def get_my_memory(agent: AgentModel = Depends(verify_agent)):
    """memory 原语：读自己的记忆。"""
    path = _memory_path(agent.id)
    if not path.exists():
        return {"agent_id": agent.id, "notes": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"agent_id": agent.id, "notes": []}


@router.post("/me/memory")
def append_my_memory(req: MemoryAppendRequest, agent: AgentModel = Depends(verify_agent)):
    """memory 原语：往自己的记忆追加一条笔记。"""
    path = _memory_path(agent.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"agent_id": agent.id, "notes": []}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    data.setdefault("notes", []).append({"text": req.note, "at": datetime.utcnow().isoformat()})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
