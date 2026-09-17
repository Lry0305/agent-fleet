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
import base64

from backend.database import get_db
from backend.models import (
    AgentModel, AgentRole, TransactionModel, ServiceModel, ArtifactModel,
    TransactionStatus,
)
from backend.auth import verify_api_key, verify_boss
from backend.config import CREDIT_TO_ETH_RATE, FUNDER_PRIVATE_KEY
from backend.chain import (
    chain_available, lock_funds_on_chain, release_funds_on_chain,
    deliver_result_on_chain, get_onchain_balance,
)
from backend.routes.artifacts import store_content
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


class DeliverRequest(BaseModel):
    data_b64: str = Field(..., description="交付物内容 base64")
    content_type: str = Field(default="application/json")
    filename: str = Field(default="")


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


def create_locked_transaction(
    db: Session, consumer: AgentModel, provider: AgentModel,
    amount: float, service_name: str = "",
) -> TransactionModel:
    """锁仓：扣 consumer 余额 + 链上锁 ETH + 建交易记录（FUND_LOCKED）。

    pay 端点与编排层（jobs 调度器派发 task 时）共用同一份逻辑。
    """
    consumer.total_spent += amount

    chain_tx_hash, chain_request_id = "", 0
    if chain_available() and consumer.encrypted_private_key and provider.eth_address:
        try:
            info = lock_funds_on_chain(consumer.encrypted_private_key, provider.eth_address, amount)
            if info:
                chain_tx_hash, chain_request_id = info["tx_hash"], info["request_id"]
        except Exception:
            # 链上失败不阻塞 off-chain 流程
            pass

    tx = TransactionModel(
        consumer_id=consumer.id,
        provider_id=provider.id,
        service_name=service_name,
        amount=amount,
        status=TransactionStatus.FUND_LOCKED,
        chain_tx_hash=chain_tx_hash,
        chain_request_id=chain_request_id,
    )
    db.add(tx)
    return tx


def confirm_and_release(
    db: Session, consumer: AgentModel, provider: AgentModel, tx: TransactionModel,
    settle_amount: float = None,
) -> str:
    """确认交付：provider 入账 + 链上释放 ETH。返回 chain_confirm_hash。

    confirm 端点与编排层（task 完成自动结算）共用。

    settle_amount：按 token 实际结算的价（≤ tx.amount，tx.amount 是派单时锁仓的上限）。
    留空就是老路径——直接全额释放 tx.amount（/api/billing/confirm/{tx} 手动确认走这条，
    没有"任务交付物"这个概念可以拿来数 token，只能按锁仓额全给）。传了就按真实用量结算，
    锁多了的部分退还给 consumer——总量守恒，不会凭空多退或多扣。
    """
    amount = tx.amount if settle_amount is None else min(settle_amount, tx.amount)
    if amount < tx.amount:
        consumer.total_spent -= (tx.amount - amount)  # 退还没用完的锁仓余量
    provider.total_earned += amount
    tx.amount = amount  # 落库成真实结算价，交易记录里看到的就是最终真花的钱

    chain_confirm_hash = ""
    if (
        chain_available()
        and consumer.encrypted_private_key
        and provider.encrypted_private_key
        and tx.chain_tx_hash
    ):
        try:
            if tx.status == TransactionStatus.FUND_LOCKED:
                # 尚未交付 → 补 deliverResult 存证（旧流程兜底）
                data_hash = tx.data_hash or Web3.keccak(text=tx.id).hex()
                deliver_result_on_chain(provider.encrypted_private_key, tx.chain_request_id, data_hash)
            r = release_funds_on_chain(consumer.encrypted_private_key, tx.chain_request_id)
            if r:
                chain_confirm_hash = r["tx_hash"]
        except Exception:
            pass

    tx.status = TransactionStatus.CONFIRMED
    if chain_confirm_hash:
        tx.chain_tx_hash = chain_confirm_hash
    tx.updated_at = datetime.utcnow()
    return chain_confirm_hash


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
    if agent.role not in (AgentRole.CONSUMER, AgentRole.BOSS):
        raise HTTPException(status_code=403, detail="只有发起 Agent / 老板可以发起支付")

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

    tx = create_locked_transaction(db, agent, provider, req.amount, service_name)
    db.commit()
    db.refresh(tx)

    return {
        "message": f"支付成功！已锁定 {req.amount} credits → {provider.name}",
        "transaction_id": tx.id,
        "new_balance": _compute_balance(agent),
        "provider_name": provider.name,
        "service_name": service_name,
        "status": tx.status.value if isinstance(tx.status, TransactionStatus) else tx.status,
        "chain_tx_hash": tx.chain_tx_hash,
        "chain_locked": bool(tx.chain_tx_hash),
        "eth_locked": round(req.amount * CREDIT_TO_ETH_RATE, 6) if tx.chain_tx_hash else 0,
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

    if tx.status not in (TransactionStatus.FUND_LOCKED, TransactionStatus.DELIVERED):
        raise HTTPException(status_code=400, detail="该交易不处于待确认状态")

    # 转入 Provider
    provider = db.query(AgentModel).filter(AgentModel.id == tx.provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    chain_confirm_hash = confirm_and_release(db, agent, provider, tx)
    db.commit()

    return {
        "message": f"已确认！{tx.amount} credits 已转入 {provider.name}",
        "transaction_id": tx.id,
        "provider_new_balance": _compute_balance(provider),
        "chain_confirm_hash": chain_confirm_hash,
    }


@router.post("/deliver/{tx_id}")
def deliver_result(
    tx_id: str,
    req: DeliverRequest,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Provider 交付：存交付物（内容寻址）→ data_hash = cid → 链上存证 → status=DELIVERED。
    交付物 cid 与链上 dataHash、交易的 data_hash 三者一致。
    """
    tx = db.query(TransactionModel).filter(TransactionModel.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="交易不存在")
    if tx.provider_id != agent.id:
        raise HTTPException(status_code=403, detail="只有 Provider 可以交付")
    if tx.status not in (TransactionStatus.FUND_LOCKED, TransactionStatus.DELIVERED):
        raise HTTPException(status_code=400, detail="该交易不接受交付")

    try:
        data = base64.b64decode(req.data_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="data_b64 不是合法 base64")

    cid = store_content(db, agent.id, data, req.content_type, req.filename)
    tx.data_hash = cid
    tx.status = TransactionStatus.DELIVERED
    tx.updated_at = datetime.utcnow()

    chain_deliver_hash = ""
    if chain_available() and agent.encrypted_private_key and tx.chain_tx_hash:
        try:
            r = deliver_result_on_chain(agent.encrypted_private_key, tx.chain_request_id, cid)
            if r:
                chain_deliver_hash = r["tx_hash"]
        except Exception:
            pass

    db.commit()
    return {
        "message": "交付完成，内容哈希已上链存证",
        "cid": cid,
        "data_hash": cid,
        "chain_deliver_hash": chain_deliver_hash,
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
def platform_stats(boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """老板名下的统计：只统计这个老板自己创建的 agent + 名下（作为 consumer/provider）的结算流水。

    之前这里是不分老板的全平台聚合（db.query(AgentModel).count() 之类，没有按 boss_id 过滤），
    会把其他账号、以及测试脚本跑出来的数据也算进去——看起来就像是假的模拟数据，其实是统计口径的 bug。
    """
    my_agents = db.query(AgentModel).filter(AgentModel.boss_id == boss.id).all()
    total_agents = len(my_agents)
    total_providers = sum(1 for a in my_agents if a.role == AgentRole.PROVIDER)
    total_consumers = sum(1 for a in my_agents if a.role == AgentRole.CONSUMER)

    owned_ids = [boss.id] + [a.id for a in my_agents]
    tx_q = db.query(TransactionModel).filter(
        TransactionModel.consumer_id.in_(owned_ids) | TransactionModel.provider_id.in_(owned_ids)
    )
    total_txs = tx_q.count()
    confirmed_txs = tx_q.filter(TransactionModel.status == TransactionStatus.CONFIRMED).count()

    # 总交易额（只算老板名下的，且只算已确认的）
    from sqlalchemy import func
    total_volume = db.query(func.sum(TransactionModel.amount)).filter(
        (TransactionModel.consumer_id.in_(owned_ids) | TransactionModel.provider_id.in_(owned_ids)),
        TransactionModel.status == TransactionStatus.CONFIRMED,
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
                funder = Account.from_key(FUNDER_PRIVATE_KEY)
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
                signed = w3.eth.account.sign_transaction(tx, FUNDER_PRIVATE_KEY)
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
