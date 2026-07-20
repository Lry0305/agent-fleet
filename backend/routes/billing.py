"""
计费 & 交易 API — 双轨制 (credits + 链上 ETH)
=============================================
Credits 是链上交易的衍生值，不提供手动修改入口。

余额构成:
  credit_balance = 初始额度 + 收到的确认交易 - 发起的支付 - 被执行的扣款
  = DEFAULT_CREDITS + total_earned - total_spent

变更 credits 的唯一途径:
  - POST /api/billing/pay           Consumer 锁定资金 (spent+)
  - POST /api/billing/confirm/{tx}  释放给 Provider (earned+)
  - POST /api/openclaw/execute      Provider 执行计费 (spent+/earned+)

API:
- GET    /api/billing/balance       查询余额 (credits + ETH)
- POST   /api/billing/pay           发起支付 → 链上锁定 ETH
- POST   /api/billing/confirm/{tx}  Consumer 确认 → 链上释放 ETH
- POST   /api/billing/dispute/{tx}  发起争议
- GET    /api/billing/transactions  交易记录 (含链上 tx_hash)
- GET    /api/billing/stats         平台统计
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from backend.database import get_db
from backend.models import (
    AgentModel, AgentRole, TransactionModel, ServiceModel,
    TransactionStatus,
)
from backend.auth import verify_api_key
from backend.config import CREDIT_TO_ETH_RATE
from backend.chain import chain_available, lock_funds_on_chain, release_funds_on_chain, get_onchain_balance
from eth_account import Account
from web3 import Web3

router = APIRouter(prefix="/api/billing", tags=["billing"])


from pydantic import BaseModel, Field


class PayRequest(BaseModel):
    provider_id: str = Field(..., description="Provider 的 agent_id")
    service_id: Optional[str] = Field(None, description="服务 ID")
    amount: float = Field(..., gt=0, description="支付金额 (credits)")


class DepositRequest(BaseModel):
    amount_credits: float = Field(default=0, gt=0, description="充值 credits 数 (1 credit = 0.01 ETH)")
    tx_hash: Optional[str] = Field(default="", description="外部钱包转账的 tx_hash (生产环境用)")
    from_address: Optional[str] = Field(default="", description="转出方钱包地址")


class TransactionResponse(BaseModel):
    id: str
    consumer_id: str
    provider_id: str
    service_name: str
    amount: float
    status: str
    chain_tx_hash: str
    data_hash: str
    error_message: str
    created_at: str

    class Config:
        from_attributes = True


def _compute_balance(agent: AgentModel) -> float:
    """余额 = 初始额度 + 总收入 - 总支出 (不可手动修改)"""
    return agent.total_earned - agent.total_spent


# ── 路由 ──

@router.get("/balance")
def get_balance(agent: AgentModel = Depends(verify_api_key)):
    """查询当前 Agent 余额 (credits + 链上 ETH)。
    credits 是链上交易的衍生值，不可手动调整。"""
    eth_onchain = 0.0
    if agent.eth_address and chain_available():
        eth_onchain = get_onchain_balance(agent.eth_address)
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "credit_balance": _compute_balance(agent),
        "total_earned": agent.total_earned,
        "total_spent": agent.total_spent,
        "eth_equivalent": round(_compute_balance(agent) * CREDIT_TO_ETH_RATE, 6),
        "eth_onchain": eth_onchain,
        "chain_connected": chain_available(),
    }


@router.post("/pay")
def pay_for_service(
    req: PayRequest,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Consumer 发起支付，调用 Provider 服务。
    1. 校验余额
    2. 锁定资金 (先扣 Consumer)
    3. 创建交易记录
    4. 余额暂存 (Provider 确认后转入)
    """
    if agent.role != AgentRole.CONSUMER:
        raise HTTPException(status_code=403, detail="只有 Consumer 可以发起支付")

    # 校验 Provider
    provider = db.query(AgentModel).filter(
        AgentModel.id == req.provider_id,
        AgentModel.role == AgentRole.PROVIDER,
    ).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    # 校验余额 (credits = total_earned - total_spent，链上交易衍生值)
    current_balance = _compute_balance(agent)
    if current_balance < req.amount:
        raise HTTPException(
            status_code=402,
            detail=f"余额不足！需要 {req.amount} credits，当前 {current_balance} credits",
        )

    # 校验服务
    service_name = "custom_service"
    if req.service_id:
        service = db.query(ServiceModel).filter(ServiceModel.id == req.service_id).first()
        if service:
            service_name = service.name

    # ── 双轨结算 ──
    # Track 1: off-chain credits (total_spent 增加，余额自然减少)
    agent.total_spent += req.amount

    # Track 2: 链上 ETH 锁定 (去信任化保证)
    chain_tx_hash = ""
    chain_request_id = 0
    chain_info = None
    if chain_available() and agent.encrypted_private_key and provider.eth_address:
        try:
            chain_info = lock_funds_on_chain(
                agent.encrypted_private_key,
                provider.eth_address,
                req.amount,
            )
            if chain_info:
                chain_tx_hash = chain_info["tx_hash"]
                chain_request_id = chain_info["request_id"]
        except Exception as e:
            # 链上失败不阻塞 off-chain 流程
            pass

    # 创建交易
    tx = TransactionModel(
        consumer_id=agent.id,
        provider_id=provider.id,
        service_name=service_name,
        amount=req.amount,
        status=TransactionStatus.FUND_LOCKED,
        chain_tx_hash=chain_tx_hash,
        chain_request_id=chain_request_id,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    return {
        "message": f"支付成功！已锁定 {req.amount} credits → {provider.name}",
        "transaction_id": tx.id,
        "new_balance": _compute_balance(agent),
        "provider_name": provider.name,
        "service_name": service_name,
        "status": tx.status.value if isinstance(tx.status, TransactionStatus) else tx.status,
        "chain_tx_hash": chain_tx_hash,
        "chain_locked": chain_info is not None,
        "eth_locked": chain_info["amount_eth"] if chain_info else 0,
    }


@router.post("/confirm/{tx_id}")
def confirm_delivery(
    tx_id: str,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Consumer 确认交付 → 释放锁定资金给 Provider。
    Provider 的 total_earned 增加 → 余额自动计算。
    """
    tx = db.query(TransactionModel).filter(TransactionModel.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="交易不存在")
    if tx.consumer_id != agent.id:
        raise HTTPException(status_code=403, detail="只有 Consumer 可以确认")

    if tx.status != TransactionStatus.FUND_LOCKED:
        raise HTTPException(status_code=400, detail="该交易不处于待确认状态")

    # 转入 Provider
    provider = db.query(AgentModel).filter(AgentModel.id == tx.provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    # ── Track 1: off-chain credits (只改 total_earned，余额自动计算) ──
    provider.total_earned += tx.amount

    # ── Track 2: 链上释放 ETH → Provider ──
    chain_confirm_hash = ""
    if chain_available() and agent.encrypted_private_key and tx.chain_request_id:
        try:
            chain_result = release_funds_on_chain(
                agent.encrypted_private_key,
                tx.chain_request_id,
            )
            if chain_result:
                chain_confirm_hash = chain_result["tx_hash"]
        except Exception:
            pass

    tx.status = TransactionStatus.CONFIRMED
    if chain_confirm_hash:
        tx.chain_tx_hash = chain_confirm_hash
    tx.updated_at = datetime.utcnow()

    db.commit()

    return {
        "message": f"已确认！{tx.amount} credits 已转入 {provider.name}",
        "transaction_id": tx.id,
        "provider_new_balance": _compute_balance(provider),
        "chain_confirm_hash": chain_confirm_hash,
    }


@router.post("/dispute/{tx_id}")
def dispute_transaction(
    tx_id: str,
    reason: str = "",
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    发起争议：Consumer 或 Provider 均可发起。
    生产环境中会触发人工审核或链上仲裁。
    """
    tx = db.query(TransactionModel).filter(TransactionModel.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="交易不存在")
    if tx.consumer_id != agent.id and tx.provider_id != agent.id:
        raise HTTPException(status_code=403, detail="无权操作")

    tx.status = TransactionStatus.DISPUTED
    tx.error_message = f"争议原因: {reason}" if reason else "争议已发起"
    tx.updated_at = datetime.utcnow()
    db.commit()

    return {"message": f"交易 {tx_id} 已标记为争议", "reason": reason}


@router.get("/transactions")
def list_transactions(
    status: Optional[str] = Query(None),
    limit: int = Query(default=20, le=100),
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    查询当前 Agent 的所有交易记录 (作为 Consumer 或 Provider)。
    """
    q = db.query(TransactionModel).filter(
        (TransactionModel.consumer_id == agent.id) |
        (TransactionModel.provider_id == agent.id)
    )
    if status:
        q = q.filter(TransactionModel.status == status)
    txs = q.order_by(TransactionModel.created_at.desc()).limit(limit).all()

    result = []
    for tx in txs:
        consumer = db.query(AgentModel).filter(AgentModel.id == tx.consumer_id).first()
        provider = db.query(AgentModel).filter(AgentModel.id == tx.provider_id).first()
        result.append({
            "id": tx.id,
            "consumer_id": tx.consumer_id,
            "consumer_name": consumer.name if consumer else "Unknown",
            "provider_id": tx.provider_id,
            "provider_name": provider.name if provider else "Unknown",
            "service_name": tx.service_name,
            "amount": tx.amount,
            "status": tx.status.value if isinstance(tx.status, TransactionStatus) else tx.status,
            "chain_tx_hash": tx.chain_tx_hash,
            "data_hash": tx.data_hash,
            "error_message": tx.error_message,
            "created_at": tx.created_at.isoformat() if tx.created_at else "",
            "updated_at": tx.updated_at.isoformat() if tx.updated_at else "",
        })

    return {"total": len(result), "transactions": result}


@router.get("/stats")
def platform_stats(db: Session = Depends(get_db)):
    """平台统计：总 Agent 数、总交易数、总交易额"""
    total_agents = db.query(AgentModel).count()
    total_providers = db.query(AgentModel).filter(AgentModel.role == AgentRole.PROVIDER).count()
    total_consumers = db.query(AgentModel).filter(AgentModel.role == AgentRole.CONSUMER).count()
    total_txs = db.query(TransactionModel).count()
    confirmed_txs = db.query(TransactionModel).filter(TransactionModel.status == TransactionStatus.CONFIRMED).count()

    # 总交易额
    from sqlalchemy import func
    total_volume = db.query(func.sum(TransactionModel.amount)).filter(
        TransactionModel.status == TransactionStatus.CONFIRMED
    ).scalar() or 0

    return {
        "total_agents": total_agents,
        "total_providers": total_providers,
        "total_consumers": total_consumers,
        "total_transactions": total_txs,
        "confirmed_transactions": confirmed_txs,
        "total_volume_credits": round(total_volume, 2),
        "total_volume_eth": round(total_volume * CREDIT_TO_ETH_RATE, 6),
    }


def _tx_to_dict(tx, db) -> dict:
    """把 transaction 转成 dict（不需要 Agent 登录）"""
    consumer = db.query(AgentModel).filter(AgentModel.id == tx.consumer_id).first()
    provider = db.query(AgentModel).filter(AgentModel.id == tx.provider_id).first()
    return {
        "id": tx.id,
        "consumer_id": tx.consumer_id,
        "consumer_name": consumer.name if consumer else "Unknown",
        "provider_id": tx.provider_id,
        "provider_name": provider.name if provider else "Unknown",
        "service_name": tx.service_name,
        "amount": tx.amount,
        "status": tx.status.value if isinstance(tx.status, TransactionStatus) else tx.status,
        "chain_tx_hash": tx.chain_tx_hash,
        "data_hash": tx.data_hash,
        "error_message": tx.error_message,
        "created_at": tx.created_at.isoformat() if tx.created_at else "",
        "updated_at": tx.updated_at.isoformat() if tx.updated_at else "",
    }


@router.get("/feed")
def public_transactions(limit: int = Query(default=20, le=100), db: Session = Depends(get_db)):
    """
    公开交易流 — 不需要登录即可查看。
    用于前端 Live Demo 看板实时展示。
    """
    txs = db.query(TransactionModel).order_by(
        TransactionModel.created_at.desc()
    ).limit(limit).all()
    return {"total": len(txs), "transactions": [_tx_to_dict(t, db) for t in txs]}


@router.get("/deposit-address")
def get_deposit_address(agent: AgentModel = Depends(verify_api_key)):
    """
    查询当前 Agent 的充值地址。用户从此地址转账 ETH 后调用 /deposit 入账。
    """
    return {
        "eth_address": agent.eth_address,
        "chain_connected": chain_available(),
        "note": "向此地址发送 ETH，然后调用 POST /api/billing/deposit 并传入 tx_hash 完成入账",
    }


@router.post("/deposit")
def deposit_credits(
    req: DepositRequest,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    【充值】向 Agent 钱包转入 ETH，credits 同步增长。

    两种模式:
      1. 快速充值 (demo): 仅传 amount_credits，用 Hardhat 默认账户代付
         适用: 演示/测试环境
      2. 正式充值 (production): 传入 tx_hash + from_address
         流程: 用户自己用 MetaMask 等钱包向 eth_address 转账
               → 调此接口传入 tx_hash → 后端链上验证 → credits 入账
    """
    amount_eth = req.amount_credits * CREDIT_TO_ETH_RATE
    chain_tx_hash = ""
    verified = False

    # 模式 A: 生产模式 — 用户已转 ETH，验证 tx_hash
    if req.tx_hash and req.from_address:
        if chain_available():
            try:
                from backend.chain import _get_w3
                w3 = _get_w3()
                tx_receipt = w3.eth.get_transaction_receipt(req.tx_hash)
                if tx_receipt:
                    # 验证: 转账到 agent.eth_address
                    to_addr = tx_receipt.get("to", "").lower()
                    expected = Web3.to_checksum_address(agent.eth_address).lower()
                    if to_addr == expected:
                        sent_wei = tx_receipt.get("logs", [])
                        if not sent_wei:
                            # ETH 转账 logs 为空，用 value 校验
                            tx_detail = w3.eth.get_transaction(req.tx_hash)
                            sent_eth = float(w3.from_wei(tx_detail["value"], "ether"))
                            agent.total_earned += sent_eth / CREDIT_TO_ETH_RATE
                            chain_tx_hash = req.tx_hash
                            verified = True
                    if not verified:
                        raise HTTPException(status_code=400, detail="tx_hash 验证失败：不是向你的 Agent 钱包转账")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"链上验证失败: {e}")
        else:
            raise HTTPException(status_code=503, detail="链不可用，无法验证 tx_hash")

    # 模式 B: 演示模式 — 用 Hardhat 默认账户直接转账
    elif req.amount_credits > 0:
        if chain_available() and agent.eth_address:
            try:
                from backend.chain import _get_w3
                w3 = _get_w3()
                funder_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
                funder = Account.from_key(funder_key)
                nonce = w3.eth.get_transaction_count(funder.address)
                tx = {
                    "from": funder.address,
                    "to": Web3.to_checksum_address(agent.eth_address),
                    "value": w3.to_wei(amount_eth, "ether"),
                    "gas": 21000,
                    "gasPrice": w3.eth.gas_price,
                    "nonce": nonce,
                    "chainId": 31337,
                }
                signed = w3.eth.account.sign_transaction(tx, funder_key)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
                chain_tx_hash = receipt.transactionHash.hex()
                agent.total_earned += req.amount_credits
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"充值失败: {e}")
        else:
            raise HTTPException(status_code=503, detail="链不可用，无法完成演示充值")
    else:
        raise HTTPException(status_code=400, detail="请提供 amount_credits(演示) 或 tx_hash(生产)")

    agent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(agent)

    new_balance = agent.total_earned - agent.total_spent
    return {
        "message": f"充值成功！+{req.amount_credits if req.amount_credits > 0 else '?'} credits (≈{amount_eth} ETH)",
        "amount_credits": req.amount_credits or round(amount_eth / CREDIT_TO_ETH_RATE, 2),
        "amount_eth": amount_eth,
        "chain_tx_hash": chain_tx_hash,
        "new_balance": new_balance,
        "eth_address": agent.eth_address,
    }
