"""
消息中继 — agent ↔ agent 通信（Phase 2）
========================================
store-and-forward 邮箱模型（类比 SMTP）：Agent 签名后投递，收件方拉取。

每条消息信封都带 EIP-191 签名，中继校验 from 身份：
  - 签名能恢复出 from 的地址（不可伪造、不可否认）
  - Bearer 令牌对应的 agent == from（你只能以自己身份发）

API:
- POST /api/messages/send         投递（客户端签名）
- GET  /api/messages/inbox        我的收件箱
- GET  /api/messages/outbox       我的发件箱
- GET  /api/messages/thread/{id}  一条会话
- POST /api/messages/{id}/read    标记已读
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
import json

from backend.database import get_db
from backend.models import AgentModel, MessageModel, MessageType
from backend.auth import verify_agent
from backend.identity import address_from_did, recover_address

router = APIRouter(prefix="/api/messages", tags=["messages"])

from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    message: dict = Field(..., description="完整消息信封（含 id/from/to/thread_id/type/payload/created_at）")
    sig: str = Field(..., description="对 message 的 EIP-191 签名（0x...）")


def _message_to_dict(m: MessageModel) -> dict:
    return {
        "id": m.id,
        "thread_id": m.thread_id,
        "from": m.sender_did,
        "from_id": m.sender_id,
        "to": m.recipient_did,
        "to_id": m.recipient_id,
        "protocol_version": m.protocol_version,
        "type": m.type.value if isinstance(m.type, MessageType) else m.type,
        "payload": json.loads(m.payload_json or "{}"),
        "read": m.read,
        "created_at": m.created_at.isoformat() if m.created_at else "",
    }


@router.post("/send")
def send_message(
    req: SendMessageRequest,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    envelope = req.message
    sender_did = envelope.get("from", "")
    to_did = envelope.get("to", "")
    mtype = envelope.get("type", "")
    protocol_version = envelope.get("protocol_version", "1.0")

    if mtype not in [m.value for m in MessageType]:
        raise HTTPException(status_code=400, detail=f"未知消息类型: {mtype}")

    # 1. 签名校验：恢复出的地址 == from 的地址
    recovered = recover_address(envelope, req.sig)
    if recovered.lower() != address_from_did(sender_did).lower():
        raise HTTPException(status_code=401, detail="签名与发送方身份不符")

    # 2. 令牌校验：Bearer 对应的 agent == from（只能以自己身份发）
    if agent.did != sender_did:
        raise HTTPException(status_code=403, detail="只能以自己的身份发送消息")

    # 3. 解析收件方
    recipient = (
        db.query(AgentModel)
        .filter(AgentModel.eth_address.ilike(address_from_did(to_did)))
        .first()
    )
    if not recipient:
        raise HTTPException(status_code=404, detail="收件方 DID 未注册")

    # 4. 能力协商：收件方 manifest.speaks 未声明该动词 → 拒绝（可扩展的守门）
    speaks = recipient.get_manifest().get("speaks", [])
    if speaks and mtype not in speaks:
        raise HTTPException(
            status_code=400,
            detail=f"收件方不识别该消息类型: {mtype}（其 speaks={speaks}）",
        )

    msg = MessageModel(
        id=envelope.get("id") or f"msg_{__import__('uuid').uuid4().hex[:12]}",
        thread_id=envelope.get("thread_id", ""),
        sender_id=agent.id,
        sender_did=sender_did,
        recipient_id=recipient.id,
        recipient_did=to_did,
        protocol_version=protocol_version,
        type=MessageType(mtype),
        payload_json=json.dumps(envelope.get("payload", {}), ensure_ascii=False),
        sig=req.sig,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {
        "message_id": msg.id,
        "thread_id": msg.thread_id,
        "protocol_version": protocol_version,
        "delivered": True,
    }


@router.get("/inbox")
def inbox(
    unread_only: bool = Query(False),
    limit: int = Query(default=50, le=200),
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    q = db.query(MessageModel).filter(MessageModel.recipient_id == agent.id)
    if unread_only:
        q = q.filter(MessageModel.read == False)  # noqa: E712
    msgs = q.order_by(MessageModel.created_at.desc()).limit(limit).all()
    return {"total": len(msgs), "messages": [_message_to_dict(m) for m in msgs]}


@router.get("/outbox")
def outbox(
    limit: int = Query(default=50, le=200),
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    msgs = (
        db.query(MessageModel)
        .filter(MessageModel.sender_id == agent.id)
        .order_by(MessageModel.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"total": len(msgs), "messages": [_message_to_dict(m) for m in msgs]}


@router.get("/thread/{thread_id}")
def thread(
    thread_id: str,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    msgs = (
        db.query(MessageModel)
        .filter(
            MessageModel.thread_id == thread_id,
            (MessageModel.sender_id == agent.id) | (MessageModel.recipient_id == agent.id),
        )
        .order_by(MessageModel.created_at.asc())
        .all()
    )
    return {"thread_id": thread_id, "messages": [_message_to_dict(m) for m in msgs]}


@router.post("/{message_id}/read")
def mark_read(
    message_id: str,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    msg = (
        db.query(MessageModel)
        .filter(MessageModel.id == message_id, MessageModel.recipient_id == agent.id)
        .first()
    )
    if not msg:
        raise HTTPException(status_code=404, detail="消息不存在")
    msg.read = True
    db.commit()
    return {"message_id": message_id, "read": True}
