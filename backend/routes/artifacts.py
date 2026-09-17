"""
交付物 — 内容寻址存储（Phase 2）
================================
cid = keccak256(content)。地址即内容哈希：不可变、可校验、可寻址。
存储后端 = 本地对象目录（可插拔为 IPFS / S3，不改接口）。

API:
- POST /api/artifacts          存储内容 → 返回 cid
- GET  /api/artifacts/{cid}    按哈希取回内容（公开，cid 即能力凭证）
"""

import base64
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from web3 import Web3

from backend.database import get_db
from backend.models import AgentModel, ArtifactModel
from backend.auth import verify_agent

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

STORE_DIR = Path(__file__).resolve().parent.parent.parent / "artifacts_store"
STORE_DIR.mkdir(exist_ok=True)

from pydantic import BaseModel, Field


class StoreRequest(BaseModel):
    data_b64: str = Field(..., description="base64 编码的内容字节")
    content_type: str = Field(default="application/json")
    filename: str = Field(default="")


def content_hash(data: bytes) -> str:
    return "0x" + Web3.keccak(data).hex()


def store_content(
    db: Session,
    creator_id: str,
    data: bytes,
    content_type: str = "application/json",
    filename: str = "",
) -> str:
    """写入对象目录 + 记元数据，返回 cid。幂等：同内容同 cid。"""
    cid = content_hash(data)
    (STORE_DIR / cid[2:]).write_bytes(data)
    if not db.query(ArtifactModel).filter(ArtifactModel.cid == cid).first():
        db.add(ArtifactModel(
            cid=cid,
            creator_id=creator_id,
            content_type=content_type,
            filename=filename,
            size=len(data),
        ))
        db.commit()
    return cid


@router.post("")
def store_artifact(
    req: StoreRequest,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    try:
        data = base64.b64decode(req.data_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="data_b64 不是合法 base64")

    cid = store_content(db, agent.id, data, req.content_type, req.filename)
    return {"cid": cid, "size": len(data), "content_type": req.content_type}


@router.get("/{cid}")
def get_artifact(cid: str, db: Session = Depends(get_db)):
    row = db.query(ArtifactModel).filter(ArtifactModel.cid == cid).first()
    if not row:
        raise HTTPException(status_code=404, detail="交付物不存在")

    p = STORE_DIR / cid[2:]
    if not p.exists():
        raise HTTPException(status_code=404, detail="内容缺失")

    return Response(content=p.read_bytes(), media_type=row.content_type)
