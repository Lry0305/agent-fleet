"""
OpenClaw Webhook / CUA-Driver 集成 API
======================================
- POST   /api/openclaw/webhook       OpenClaw/飞书 回调
- POST   /api/openclaw/register-bot  绑定机器人
- POST   /api/openclaw/execute       CUA-driver 执行任务
- POST   /api/openclaw/charge        自动计费
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from backend.database import get_db
from backend.models import (
    AgentModel, AgentRole, TransactionModel, ServiceModel,
    TransactionStatus, OpenClawBot,
)
from backend.auth import verify_api_key, verify_webhook_signature
from backend.config import OPENCLAW_WEBHOOK_SECRET

router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])

from pydantic import BaseModel, Field


class RegisterBotRequest(BaseModel):
    bot_token: str = Field(...)
    platform: str = Field(default="openclaw")
    webhook_url: Optional[str] = Field(default="")


class ExecuteTaskRequest(BaseModel):
    task_type: str = Field(...)
    target: str = Field(...)
    parameters: dict = Field(default_factory=dict)
    service_id: Optional[str] = None
    consumer_id: Optional[str] = None
    charge_amount: Optional[float] = Field(None)


class ChargeRequest(BaseModel):
    transaction_id: str = Field(...)
    data_hash: str = Field(default="")


@router.post("/register-bot")
def register_bot(
    req: RegisterBotRequest,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """绑定 OpenClaw/飞书 机器人到 Agent"""
    existing = db.query(OpenClawBot).filter(
        OpenClawBot.agent_id == agent.id,
        OpenClawBot.platform == req.platform,
    ).first()
    if existing:
        existing.bot_token = req.bot_token
        existing.webhook_url = req.webhook_url or existing.webhook_url
        existing.is_active = True
        db.commit()
        return {"message": "机器人绑定已更新"}

    bot = OpenClawBot(
        agent_id=agent.id, bot_token=req.bot_token,
        platform=req.platform, webhook_url=req.webhook_url or "",
    )
    db.add(bot)
    agent.platform = req.platform
    agent.updated_at = datetime.utcnow()
    db.commit()
    return {"message": f"机器人绑定成功 [{agent.name}] via {req.platform}"}


@router.post("/webhook")
async def openclaw_webhook(
    request: Request,
    x_signature: Optional[str] = Header(None, alias="X-OpenClaw-Signature"),
    db: Session = Depends(get_db),
):
    """接收外部 Webhook 回调 (不直接修改余额)"""
    body = await request.body()
    body_str = body.decode("utf-8")

    if x_signature:
        if not verify_webhook_signature(x_signature, body_str, OPENCLAW_WEBHOOK_SECRET):
            raise HTTPException(status_code=401, detail="Webhook 签名验证失败")

    import json
    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="无效的 JSON")

    event = payload.get("event", "")
    agent_id = payload.get("agent_id", "")
    amount = payload.get("amount", 0)

    if event == "task_completed" and agent_id and amount:
        agent = db.query(AgentModel).filter(AgentModel.id == agent_id).first()
        if agent:
            # Webhook 只记录事件，不直接修改余额
            # 余额只能通过 pay → confirm / execute 交易流变更
            agent.updated_at = datetime.utcnow()
            db.commit()

    return {"event": event, "status": "received"}


@router.post("/execute")
def execute_task(
    req: ExecuteTaskRequest,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    CUA-driver / Agent 执行任务并自动计费。

    生产环境:
    - macOS: CUA-driver (claude computer-use) 操控桌面
    - 飞书: OpenClaw SDK 收发消息
    - Web: HTTP API 直接调用
    """
    if agent.role != AgentRole.PROVIDER:
        raise HTTPException(status_code=403, detail="只有 Provider 可以执行任务")

    if not req.consumer_id or not req.charge_amount:
        return {
            "message": "任务已模拟执行",
            "task_type": req.task_type,
            "target": req.target,
            "note": "未提供 consumer_id/charge_amount，跳过计费",
        }

    consumer = db.query(AgentModel).filter(AgentModel.id == req.consumer_id).first()
    if not consumer:
        raise HTTPException(status_code=404, detail="Consumer 不存在")

    # 余额 = total_earned - total_spent (链上交易衍生值)
    consumer_balance = consumer.total_earned - consumer.total_spent
    if consumer_balance < req.charge_amount:
        raise HTTPException(status_code=402, detail=f"Consumer {consumer.name} 余额不足 (需要 {req.charge_amount}, 当前 {consumer_balance})")

    svc_name = "custom_task"
    if req.service_id:
        svc = db.query(ServiceModel).filter(ServiceModel.id == req.service_id).first()
        if svc:
            svc_name = svc.name

    # 只改 total_spent/total_earned，余额自动计算
    consumer.total_spent += req.charge_amount
    agent.total_earned += req.charge_amount

    tx = TransactionModel(
        consumer_id=consumer.id, provider_id=agent.id,
        service_name=svc_name, amount=req.charge_amount,
        status=TransactionStatus.CONFIRMED,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    return {
        "message": f"任务完成！{consumer.name} → {agent.name} 支付 {req.charge_amount} credits",
        "transaction_id": tx.id,
        "provider_balance": agent.total_earned - agent.total_spent,
    }


@router.post("/charge")
def confirm_charge(
    req: ChargeRequest,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Provider 交付后确认计费"""
    tx = db.query(TransactionModel).filter(TransactionModel.id == req.transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="交易不存在")
    if tx.provider_id != agent.id:
        raise HTTPException(status_code=403, detail="只有 Provider 可以确认")

    provider = db.query(AgentModel).filter(AgentModel.id == tx.provider_id).first()
    if provider:
        provider.total_earned += tx.amount

    tx.status = TransactionStatus.CONFIRMED
    tx.data_hash = req.data_hash
    tx.updated_at = datetime.utcnow()
    db.commit()

    return {
        "message": "计费确认完成",
        "amount": tx.amount,
        "provider_new_balance": provider.total_earned - provider.total_spent if provider else 0,
    }
