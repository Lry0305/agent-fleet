"""
服务市场 API
=============
- GET    /api/services           服务列表 / 搜索
- POST   /api/services/register   Provider 注册服务
- PUT    /api/services/{id}       Provider 更新服务
- DELETE /api/services/{id}       Provider 下架服务
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from backend.database import get_db
from backend.models import ServiceModel, AgentModel, AgentRole
from backend.auth import verify_api_key

router = APIRouter(prefix="/api/services", tags=["services"])


from pydantic import BaseModel, Field


class ServiceRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: Optional[str] = Field(default="")
    endpoint: Optional[str] = Field(default="")
    price: float = Field(default=0.0, ge=0.0)
    chain_service_id: Optional[int] = Field(default=0)


class ServiceUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    endpoint: Optional[str] = None
    price: Optional[float] = None
    is_active: Optional[bool] = None


def service_to_dict(s: ServiceModel) -> dict:
    return {
        "id": s.id,
        "provider_id": s.provider_id,
        "name": s.name,
        "description": s.description,
        "endpoint": s.endpoint,
        "price": s.price,
        "is_active": s.is_active,
        "chain_service_id": s.chain_service_id,
        "created_at": s.created_at.isoformat() if s.created_at else "",
        "updated_at": s.updated_at.isoformat() if s.updated_at else "",
    }


@router.get("")
def list_services(
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    provider_id: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    active_only: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """
    服务市场列表。支持关键词搜索、价格筛选。
    """
    q = db.query(ServiceModel)
    if active_only:
        q = q.filter(ServiceModel.is_active == True)
    if keyword:
        q = q.filter(
            (ServiceModel.name.ilike(f"%{keyword}%")) |
            (ServiceModel.description.ilike(f"%{keyword}%"))
        )
    if provider_id:
        q = q.filter(ServiceModel.provider_id == provider_id)
    if min_price is not None:
        q = q.filter(ServiceModel.price >= min_price)
    if max_price is not None:
        q = q.filter(ServiceModel.price <= max_price)

    services = q.order_by(ServiceModel.created_at.desc()).all()

    # 关联 Provider 名称
    result = []
    for s in services:
        provider = db.query(AgentModel).filter(AgentModel.id == s.provider_id).first()
        result.append({
            **service_to_dict(s),
            "provider_name": provider.name if provider else "Unknown",
            "provider_avatar": provider.avatar if provider else "🤖",
        })

    return {"total": len(result), "services": result}


@router.post("/register")
def register_service(
    req: ServiceRegisterRequest,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Provider 注册新服务 (需认证)"""
    if agent.role != AgentRole.PROVIDER:
        raise HTTPException(status_code=403, detail="只有 Provider 可以注册服务")

    service = ServiceModel(
        provider_id=agent.id,
        name=req.name,
        description=req.description or "",
        endpoint=req.endpoint or "",
        price=req.price,
        chain_service_id=req.chain_service_id or 0,
    )
    db.add(service)

    # 同步更新 Agent 的默认服务信息
    if not agent.service_name:
        agent.service_name = req.name
    if not agent.service_description:
        agent.service_description = req.description or ""
    agent.price_per_call = req.price

    db.commit()
    db.refresh(service)

    return {"message": f"服务 [{req.name}] 注册成功", **service_to_dict(service)}


@router.post("/register-onchain")
def register_service_onchain(
    req: ServiceRegisterRequest,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    【链上注册服务】调用 ServiceRegistry 合约的 registerService(),
    同时把记录同步到 SQLite。

    注意：chain 端 price 单位是 wei (1 ETH = 1e18 wei)
    """
    if agent.role != AgentRole.PROVIDER:
        raise HTTPException(status_code=403, detail="只有 Provider 可以注册服务")
    if not agent.encrypted_private_key:
        raise HTTPException(status_code=400, detail="Agent 缺少链上钱包私钥")

    from backend.chain import register_service_onchain as chain_register, chain_available
    from backend.config import CREDIT_TO_ETH_RATE

    price_eth = req.price * CREDIT_TO_ETH_RATE
    chain_info = None
    if chain_available():
        chain_info = chain_register(
            agent.encrypted_private_key,
            req.name,
            req.description or "",
            price_eth,
        )

    if not chain_info:
        return {
            "success": False,
            "message": "链上注册失败 (合约未部署/钱包无 ETH/网络问题)",
            "offline_registered": False,
        }

    # 链上成功 → 同步到 SQLite
    service = ServiceModel(
        provider_id=agent.id,
        name=req.name,
        description=req.description or "",
        endpoint=req.endpoint or "",
        price=req.price,
        chain_service_id=chain_info["service_id"],
    )
    db.add(service)
    if not agent.service_name:
        agent.service_name = req.name
    agent.price_per_call = req.price
    db.commit()
    db.refresh(service)

    return {
        "success": True,
        "message": f"✅ 链上注册成功！服务ID: {chain_info['service_id']}",
        "service": service_to_dict(service),
        "chain_tx_hash": chain_info["tx_hash"],
        "chain_service_id": chain_info["service_id"],
    }


@router.put("/{service_id}")
def update_service(
    service_id: str,
    req: ServiceUpdateRequest,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Provider 更新自己的服务"""
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="服务不存在")
    if service.provider_id != agent.id:
        raise HTTPException(status_code=403, detail="只能编辑自己的服务")

    update_fields = req.model_dump(exclude_none=True)
    for field, value in update_fields.items():
        setattr(service, field, value)
    service.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(service)

    return {"message": "服务更新成功", **service_to_dict(service)}


@router.delete("/{service_id}")
def deactivate_service(
    service_id: str,
    agent: AgentModel = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Provider 下架服务 (软删除)"""
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="服务不存在")
    if service.provider_id != agent.id:
        raise HTTPException(status_code=403, detail="只能操作自己的服务")

    service.is_active = False
    service.updated_at = datetime.utcnow()
    db.commit()

    return {"message": f"服务 [{service.name}] 已下架"}
