"""
事件流 — 统一时间线（Phase 2）
==============================
把 message / transaction / artifact 合并成一条 append-only 时间线，
供经营台（人，只读）可视化：人只看，不干预。

API:
- GET /api/events/stream  公开只读，limit 条
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import datetime
import json

from backend.database import get_db
from backend.models import (
    AgentModel, MessageModel, MessageType, TransactionModel,
    TransactionStatus, ArtifactModel, JobModel, TaskModel, JobStatus, TaskStatus,
)

router = APIRouter(prefix="/api/events", tags=["events"])


def _iso(dt) -> str:
    return dt.isoformat() if dt else ""


def _merge(db: Session, limit: int) -> list:
    events = []

    msgs = db.query(MessageModel).order_by(MessageModel.created_at.desc()).limit(limit).all()
    for m in msgs:
        events.append({
            "kind": "message",
            "ts": _iso(m.created_at),
            "actor": m.sender_id,
            "actor_did": m.sender_did,
            "target": m.recipient_id,
            "action": m.type.value if isinstance(m.type, MessageType) else m.type,
            "payload": json.loads(m.payload_json or "{}"),
            "thread_id": m.thread_id,
        })

    txs = db.query(TransactionModel).order_by(TransactionModel.created_at.desc()).limit(limit).all()
    for t in txs:
        events.append({
            "kind": "settlement",
            "ts": _iso(t.created_at),
            "actor": t.consumer_id,
            "target": t.provider_id,
            "action": t.status.value if isinstance(t.status, TransactionStatus) else t.status,
            "amount": t.amount,
            "service": t.service_name,
            "data_hash": t.data_hash,
            "chain_tx_hash": t.chain_tx_hash,
        })

    arts = db.query(ArtifactModel).order_by(ArtifactModel.created_at.desc()).limit(limit).all()
    for a in arts:
        events.append({
            "kind": "artifact",
            "ts": _iso(a.created_at),
            "actor": a.creator_id,
            "target": "",
            "action": "deliver",
            "cid": a.cid,
            "content_type": a.content_type,
            "size": a.size,
        })

    jobs = db.query(JobModel).order_by(JobModel.created_at.desc()).limit(limit).all()
    for j in jobs:
        events.append({
            "kind": "job",
            "ts": _iso(j.created_at),
            "actor": j.boss_id,
            "target": "",
            "action": j.status.value if isinstance(j.status, JobStatus) else j.status,
            "goal": j.goal,
            "budget": j.budget,
            "job_id": j.id,
        })

    tasks = db.query(TaskModel).order_by(TaskModel.created_at.desc()).limit(limit).all()
    for t in tasks:
        events.append({
            "kind": "task",
            "ts": _iso(t.created_at),
            "actor": t.provider_id,
            "target": "",
            "action": t.status.value if isinstance(t.status, TaskStatus) else t.status,
            "skill": t.skill,
            "job_id": t.job_id,
            "task_id": t.id,
            "price": t.price,
        })

    events.sort(key=lambda e: e["ts"], reverse=True)
    return events[:limit]


@router.get("/stream")
def event_stream(limit: int = Query(default=50, le=200), db: Session = Depends(get_db)):
    events = _merge(db, limit)

    # 附上 actor/target 的名字，供前端直接展示
    ids = {e["actor"] for e in events} | {e["target"] for e in events if e["target"]}
    names = {}
    for a in db.query(AgentModel).filter(AgentModel.id.in_(ids)).all():
        names[a.id] = a.name
    for e in events:
        e["actor_name"] = names.get(e["actor"], e["actor"])
        e["target_name"] = names.get(e["target"], e["target"]) if e["target"] else ""

    return {"total": len(events), "events": events}
