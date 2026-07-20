"""
声誉系统 API
============
Consumer 在交易完成后给 Provider 打分 (1-5 星)。

- POST   /api/reputation/rate/{tx_id}  给交易打分
- GET    /api/reputation/{provider_id}  查询 Provider 声誉
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from backend.database import get_db
from backend.models import (
    AgentModel, AgentRole, TransactionModel, TransactionStatus, ReputationModel,
)
from backend.auth import verify_agent

router = APIRouter(prefix="/api/reputation", tags=["reputation"])

from pydantic import BaseModel, Field


class RateRequest(BaseModel):
    score: int = Field(..., ge=1, le=5, description="评分 1-5")
    comment: Optional[str] = Field(default="", max_length=500)


@router.post("/rate/{tx_id}")
def rate_transaction(
    tx_id: str,
    req: RateRequest,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    """
    Consumer 给一笔已确认的交易打分。

    规则：
    - 只有该交易的 Consumer 可以打分
    - 只能对 CONFIRMED 状态的交易打分
    - 一笔交易只能打分一次
    - 评分范围 1-5
    """
    if agent.role != AgentRole.CONSUMER:
        raise HTTPException(status_code=403, detail="只有 Consumer 可以评分")

    tx = db.query(TransactionModel).filter(TransactionModel.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="交易不存在")
    if tx.consumer_id != agent.id:
        raise HTTPException(status_code=403, detail="只能评价自己的交易")
    if tx.status != TransactionStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail="只能对已确认的交易评分")

    existing = db.query(ReputationModel).filter(
        ReputationModel.transaction_id == tx_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="该交易已经评过分")

    rating = ReputationModel(
        provider_id=tx.provider_id,
        consumer_id=agent.id,
        transaction_id=tx_id,
        score=req.score,
        comment=req.comment or "",
    )
    db.add(rating)
    db.commit()
    db.refresh(rating)

    return {
        "message": f"评分成功！Provider 获得 {req.score}/5 星",
        "rating_id": rating.id,
        "score": req.score,
    }


@router.get("/{provider_id}")
def get_reputation(
    provider_id: str,
    db: Session = Depends(get_db),
):
    """
    查询 Provider 的声誉统计。
    不需要登录即可查看。
    优先用 SQLite 评分；如果 Provider 有 ETH 地址，也返回链上声誉作为对照。
    """
    provider = db.query(AgentModel).filter(AgentModel.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    ratings = db.query(ReputationModel).filter(
        ReputationModel.provider_id == provider_id
    ).all()

    total = len(ratings)
    avg = round(sum(r.score for r in ratings) / total, 2) if total > 0 else 0.0
    result = {
        "provider_id": provider_id,
        "provider_name": provider.name,
        "total_ratings": total,
        "average_score": avg,
        "star_display": "⭐" * round(avg),
        "ratings": [
            {
                "score": r.score,
                "comment": r.comment,
                "chain_tx_hash": r.chain_tx_hash,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in ratings[-10:][::-1]
        ],
    }

    # 链上声誉补充
    if provider.eth_address:
        from backend.chain import get_onchain_reputation
        result["onchain_reputation"] = get_onchain_reputation(provider.eth_address)

    return result


@router.post("/rate-onchain/{tx_id}")
def rate_provider_onchain(
    tx_id: str,
    req: RateRequest,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    """
    链上评分：在 Reputation 合约上记录评分 + 同步到 SQLite。
    必须是已确认交易的 Consumer。
    """
    if agent.role != AgentRole.CONSUMER:
        raise HTTPException(status_code=403, detail="只有 Consumer 可以评分")

    tx = db.query(TransactionModel).filter(TransactionModel.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="交易不存在")
    if tx.consumer_id != agent.id:
        raise HTTPException(status_code=403, detail="只能评价自己的交易")
    if tx.status != TransactionStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail="只能对已确认的交易评分")

    # 链上评分
    chain_tx_hash = ""
    provider = db.query(AgentModel).filter(AgentModel.id == tx.provider_id).first()
    if provider and provider.eth_address and agent.encrypted_private_key:
        from backend.chain import rate_provider_onchain as chain_rate, chain_available
        if chain_available():
            chain_tx_hash = chain_rate(
                agent.encrypted_private_key,
                provider.eth_address,
                req.score,
            ) or ""

    # 同步到 SQLite
    existing = db.query(ReputationModel).filter(
        ReputationModel.transaction_id == tx_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="该交易已经评过分")

    rating = ReputationModel(
        provider_id=tx.provider_id,
        consumer_id=agent.id,
        transaction_id=tx_id,
        score=req.score,
        comment=req.comment or "",
        chain_tx_hash=chain_tx_hash,
    )
    db.add(rating)
    db.commit()
    db.refresh(rating)

    return {
        "message": f"链上评分成功！{provider.name} 获得 {req.score}/5 星",
        "rating_id": rating.id,
        "score": req.score,
        "chain_tx_hash": chain_tx_hash,
    }
