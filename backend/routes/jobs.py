"""
编排层 + 控制面（Phase 3）
==========================
把「老板一句目标」拆成多任务 DAG（并行/串行），靠标准化消息协议派发 + 控制。

角色：
  - boss（人）在经营台充钱/派活/喊停/调整
  - provider 接 spawn → 跑 skill → 交付 → 结算
  - consumer 保留（agent↔agent 转包，v1 未用）

协议语言（MessageType 三平面）：
  - 工作元语：spawn / deliver / result / error / status
    （offer / accept / decline 枚举值还留着，只是给旧数据兼容用——现在派单不再发这三种
    消息，选 provider 不比价，见下面「定价」）
  - 控制元语：pause / resume / cancel / adjust
  - 协议元语：ack

定价：全平台统一费率（backend/config.py 的 GLOBAL_RATE_PER_1K，单位：credits / 1000 个
交付物 token），不是 provider 自己报的，也不是老板定的——老板/agent 都估不准一个任务
要产出多少 token，与其两边都猜数字，不如价格从一开始就是统一的。派单时任务真实产出
多少还不知道，按统一费率 × 保守输出上限自动锁一个"预授权"额度（backend/pricing.py 的
estimate_lock()，不需要老板指定任何数字）；任务交付后拿真实交付物数 token，按统一费率
结算——多退（锁多了退差价），不能超锁仓上限。

派 provider：任务适配导向，不是价格导向。显式指定 provider 时直接分配；不指定 provider
时对自己名下匹配 skill 的活跃 provider 直接指派（_find_provider_by_skill()），多个匹配
按声誉最高的来（同声誉按注册时间早的），不比价、不发竞价消息——价格全平台一样，没什么
好比的。

API:
  POST /api/jobs               老板创建 job（目标 + 任务 DAG，provider 可留空按 skill 匹配指派）
  GET  /api/jobs               列出 jobs
  GET  /api/jobs/{id}          job 详情 + tasks + 进度
  POST /api/jobs/{id}/pause|resume|cancel|adjust   老板控制
  POST /api/tasks/{id}/done     provider 交付 → DELIVERED，按策略自动验收或等老板审核（Phase 4）
  POST /api/tasks/{id}/accept   老板人工验收通过 → 结算 + 推进 DAG（Phase 4）
  POST /api/tasks/{id}/reject   老板人工验收不通过 → 退款（Phase 4）
  POST /api/tasks/{id}/fail     provider 任务失败 → 退款 + 推进 DAG
"""

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    AgentModel, AgentRole, MessageModel, MessageType,
    JobModel, TaskModel, JobStatus, TaskStatus, TransactionModel, TransactionStatus,
    ReputationModel, SkillModel, AgentSkillModel,
)
from backend.auth import verify_agent, verify_boss
from backend.routes.billing import create_locked_transaction, confirm_and_release, _compute_balance
from backend.pricing import estimate_lock, settle_price

router = APIRouter(prefix="/api", tags=["jobs"])

from pydantic import BaseModel, Field


# ── 请求模型 ──

class TaskSpec(BaseModel):
    ref: str = Field(..., description="客户端 key，供 depends_on 引用")
    provider_id: Optional[str] = Field(default=None, description="分配的 provider agent_id；留空则按 skill 直接匹配一个活跃 provider")
    skill: str = Field(..., min_length=1, description="需要的技能，用于匹配 provider")
    input: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, description="依赖的 ref 列表（DAG 边）")
    require_review: bool = Field(default=False, description="True=老板必须手动 accept/reject；False=结构校验通过即自动验收")
    # 注意：这里没有 price，也没有 max_credits —— 价格全平台统一（GLOBAL_RATE_PER_1K），
    # 老板/agent 都不用猜任务会产出多少 token；锁仓额是按统一费率自动算的，不需要指定。


class JobCreateRequest(BaseModel):
    goal: str = Field(..., description="一句目标")
    budget: float = Field(default=0.0, ge=0)
    priority: int = Field(default=0)
    tasks: list[TaskSpec] = Field(..., min_length=1)


class TaskDoneRequest(BaseModel):
    cid: str = Field(default="", description="交付物 cid")
    output: dict = Field(default_factory=dict)


class AdjustRequest(BaseModel):
    budget: float = Field(default=0.0, ge=0)


# ── 内部助手 ──

def _find_provider_by_skill(db: Session, boss: AgentModel, skill: str) -> Optional[AgentModel]:
    """任务适配导向：在老板自己名下找一个匹配 skill 的活跃 provider，直接指派——不比价
    （价格全平台统一，没什么可比的），多个匹配时按声誉最高的来（同声誉按注册时间早的，
    保证结果确定、可复现）。返回命中的 provider，没有就返回 None。

    按 boss_id 限定候选池——"一人公司 agent 群"里选人应该只在自己名下的 agent 之间发生，
    不该捞出别的老板名下的 agent（早期这里漏过这条过滤，导致跨老板的 agent 会被互相拉进
    对方的候选池，是真实存在过的 bug，不是假设）。
    """
    candidates = db.query(AgentModel).filter(
        AgentModel.role == AgentRole.PROVIDER,
        AgentModel.boss_id == boss.id,
        AgentModel.service_name == skill,
        AgentModel.is_service_active == True,  # noqa: E712
    ).all()
    if not candidates:
        return None

    def _rank(p: AgentModel):
        ratings = db.query(ReputationModel).filter(ReputationModel.provider_id == p.id).all()
        avg = sum(r.score for r in ratings) / len(ratings) if ratings else 0.0
        return (-avg, p.created_at or datetime.min)  # 声誉高者优先；同声誉按注册早的

    candidates.sort(key=_rank)
    return candidates[0]


def _refund_task(db: Session, task: TaskModel):
    """把 task 关联的锁仓/已交付未结算资金退还老板（v1 只退 off-chain，链上退款后续）。幂等。"""
    if not task.tx_id:
        return
    tx = db.query(TransactionModel).filter(TransactionModel.id == task.tx_id).first()
    if not tx or tx.status not in (TransactionStatus.FUND_LOCKED, TransactionStatus.DELIVERED):
        return
    job = db.query(JobModel).filter(JobModel.id == task.job_id).first()
    boss = db.query(AgentModel).filter(AgentModel.id == job.boss_id).first() if job else None
    if boss:
        boss.total_spent -= tx.amount
    tx.status = TransactionStatus.REFUNDED


def _accept_task(db: Session, job: JobModel, task: TaskModel):
    """验收通过：结算 + task=DONE。auto-accept 和人工 /accept 端点共用；不在这里 advance（调用方决定时机）。

    按 task.settled_price 结算（task_done() 交付时拿交付物真实 token 数算出来的），不是
    tx.amount（那是派单时锁的上限）——多锁的部分 confirm_and_release 内部会退给老板。
    """
    if task.tx_id:
        tx = db.query(TransactionModel).filter(TransactionModel.id == task.tx_id).first()
        if tx and tx.status in (TransactionStatus.FUND_LOCKED, TransactionStatus.DELIVERED):
            boss = db.query(AgentModel).filter(AgentModel.id == job.boss_id).first()
            provider = db.query(AgentModel).filter(AgentModel.id == task.provider_id).first()
            confirm_and_release(db, boss, provider, tx, settle_amount=task.settled_price)
    task.status = TaskStatus.DONE
    task.updated_at = datetime.utcnow()


def _auto_accept_check(task: TaskModel) -> bool:
    """Phase 4 v1 自动验收策略：结构校验（确有交付物）。
    可插拔点：换成 LLM-as-judge 对照 task.input_json 校验 output 语义，接口不变。
    """
    has_cid = bool(task.output_cid)
    has_output = bool(json.loads(task.output_json or "{}"))
    return has_cid or has_output


def _send_coordinator_message(
    db: Session, sender: AgentModel, recipient: Optional[AgentModel],
    type_: str, payload: dict, thread_id: str,
) -> Optional[MessageModel]:
    """协调者投递消息：平台内部消息（由 boss 的已认证 HTTP 调用触发，无需再签名）。

    agent↔agent 的消息仍走 /api/messages/send 签名校验；这里是编排层代表 boss 发控制/派发。

    recipient 可能是 None——它已经被老板销毁了，但手上挂着的 task 还没走完（真实发生过的
    bug：destroy 一个还有 ASSIGNED/RUNNING task 的 agent 之后，pause/resume/cancel/adjust
    一碰这个 task 就因为 recipient.id 在 None 上取值而整个请求 500，job 永久卡死、锁的钱也
    退不回来）。这种情况就不发消息，调用方照常推进 task 状态。
    """
    if recipient is None:
        return None
    msg = MessageModel(
        thread_id=thread_id,
        sender_id=sender.id,
        sender_did=sender.did,
        recipient_id=recipient.id,
        recipient_did=recipient.did,
        protocol_version="1.0",
        type=MessageType(type_),
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(msg)
    return msg


def _assign_task(db: Session, job: JobModel, task: TaskModel):
    """派发一个就绪 task：锁仓（escrow）→ 发 spawn → task=ASSIGNED。"""
    boss = db.query(AgentModel).filter(AgentModel.id == job.boss_id).first()
    provider = db.query(AgentModel).filter(AgentModel.id == task.provider_id).first()
    if not boss or not provider:
        task.status = TaskStatus.FAILED
        return

    if _compute_balance(boss) < task.price:
        task.status = TaskStatus.FAILED
        return

    tx = create_locked_transaction(db, boss, provider, task.price, task.skill)
    db.flush()  # 拿到 tx.id

    task.tx_id = tx.id
    task.status = TaskStatus.ASSIGNED

    _send_coordinator_message(
        db, boss, provider, "spawn",
        {
            "task_id": task.id,
            "job_id": job.id,
            "tx_id": tx.id,
            "skill": task.skill,
            "input": json.loads(task.input_json or "{}"),
            "price": task.price,
        },
        thread_id=job.id,
    )


def _advance(db: Session, job: JobModel):
    """DAG 调度器：派发就绪 task（依赖全 done）→ 更新 job 状态。幂等。"""
    tasks = db.query(TaskModel).filter(TaskModel.job_id == job.id).all()
    status_by_id = {t.id: t.status for t in tasks}

    if job.status in (JobStatus.DRAFT, JobStatus.RUNNING):
        for t in tasks:
            if t.status != TaskStatus.QUEUED:
                continue
            deps = json.loads(t.depends_on or "[]")
            if all(status_by_id.get(d, TaskStatus.DONE) == TaskStatus.DONE for d in deps):
                _assign_task(db, job, t)

    # 重新读取最新状态，更新 job 状态
    db.flush()
    tasks = db.query(TaskModel).filter(TaskModel.job_id == job.id).all()
    statuses = [t.status for t in tasks]
    if any(s in (TaskStatus.FAILED, TaskStatus.REJECTED) for s in statuses):
        job.status = JobStatus.FAILED
    elif all(s == TaskStatus.DONE for s in statuses):
        job.status = JobStatus.DONE
    elif all(s in (TaskStatus.DONE, TaskStatus.CANCELLED) for s in statuses):
        job.status = JobStatus.CANCELLED
    elif job.status != JobStatus.PAUSED:
        job.status = JobStatus.RUNNING
    job.updated_at = datetime.utcnow()


def _task_to_dict(db: Session, t: TaskModel) -> dict:
    provider = db.query(AgentModel).filter(AgentModel.id == t.provider_id).first()

    # 技能包（方法论层）交付点：worker 轮询 GET /api/tasks 拿到的就是这个函数的返回值，
    # 把 provider 当前装配的所有技能包内容一起吐出去——worker 自己决定怎么塞进调它
    # 自己 LLM 时的 prompt。跟 t.skill（派单用的 service_name/市场身份）是两回事，
    # 不要混在一起：t.skill 是"派给谁"，这里的 skills 是"派到的这个人被交代了哪些方法论"。
    installed_skills = []
    if provider:
        rows = (
            db.query(SkillModel)
            .join(AgentSkillModel, AgentSkillModel.skill_id == SkillModel.id)
            .filter(AgentSkillModel.agent_id == provider.id)
            .all()
        )
        installed_skills = [
            {
                "skill_id": s.id, "name": s.name, "description": s.description,
                "content": s.content, "local_path": s.local_path,
            }
            for s in rows
        ]

    return {
        "task_id": t.id,
        "job_id": t.job_id,
        "provider_id": t.provider_id,
        "provider_name": provider.name if provider else "?",
        "skill": t.skill,
        "input": json.loads(t.input_json or "{}"),
        "output_cid": t.output_cid,
        "output": json.loads(t.output_json or "{}"),
        "status": t.status.value if isinstance(t.status, TaskStatus) else t.status,
        "depends_on": json.loads(t.depends_on or "[]"),
        "price": t.price,           # 预授权锁仓上限，不是最终结算价
        "price_tier": t.price_tier,  # 锁仓说明文字
        "token_count": t.token_count,     # 交付物真实 token 数（交付前是 0）
        "settled_price": t.settled_price,  # 按真实 token 数结算的最终价（交付前是 0）
        "tx_id": t.tx_id,
        "require_review": t.require_review,
        "skills": installed_skills,  # provider 当前装配的技能包（方法论文本），worker 自己消费
    }


def _job_to_dict(db: Session, job: JobModel) -> dict:
    tasks = db.query(TaskModel).filter(TaskModel.job_id == job.id).all()
    done = sum(1 for t in tasks if t.status == TaskStatus.DONE)
    return {
        "job_id": job.id,
        "boss_id": job.boss_id,
        "goal": job.goal,
        "status": job.status.value if isinstance(job.status, JobStatus) else job.status,
        "budget": job.budget,
        "priority": job.priority,
        "progress": {"done": done, "total": len(tasks)},
        "tasks": [_task_to_dict(db, t) for t in tasks],
        "created_at": job.created_at.isoformat() if job.created_at else "",
    }


# ── 创建 job ──

@router.post("/jobs")
def create_job(
    req: JobCreateRequest,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    if agent.role != AgentRole.BOSS:
        raise HTTPException(status_code=403, detail="只有老板（boss）可以派活")

    job = JobModel(boss_id=agent.id, goal=req.goal, budget=req.budget, priority=req.priority)
    db.add(job)
    db.flush()  # 拿到 job.id：既是 thread_id，也用于协议消息

    # 解析 provider + 锁仓额：结算价永远来自全平台统一费率 × 交付物真实 token 数（老板/agent
    # 都定不了，也不用猜），这里先按统一费率自动算一个预授权锁仓额，所有 task 都一样。
    # 显式指定 provider 时直接分配；留空则按 skill 匹配自己名下活跃的 provider（不比价，按声誉选）。
    resolved = {}
    for spec in req.tasks:
        if spec.provider_id:
            # boss_id 必须归属当前老板——不然可以直接指定别人名下的 agent，把任务塞给不归
            # 自己管的 provider，跟 _find_provider_by_skill 漏 boss_id 过滤是同一类问题。
            p = db.query(AgentModel).filter(
                AgentModel.id == spec.provider_id, AgentModel.role == AgentRole.PROVIDER,
                AgentModel.boss_id == agent.id,
            ).first()
            if not p:
                raise HTTPException(status_code=404, detail=f"provider 不存在、不是执行 Agent，或不归你名下: {spec.ref}")
        else:
            p = _find_provider_by_skill(db, agent, spec.skill)
            if not p:
                raise HTTPException(status_code=404, detail=f"task {spec.ref}: 找不到匹配的活跃 provider（skill={spec.skill}）")
        price, tier = estimate_lock()
        resolved[spec.ref] = {"provider_id": p.id, "price": price, "tier": tier}

    # 先建所有 task，记录 ref → id 映射
    ref_to_id = {}
    for spec in req.tasks:
        r = resolved[spec.ref]
        t = TaskModel(
            job_id=job.id,
            provider_id=r["provider_id"],
            skill=spec.skill,
            input_json=json.dumps(spec.input, ensure_ascii=False),
            price=r["price"],
            price_tier=r["tier"],
            require_review=spec.require_review,
        )
        db.add(t)
        db.flush()
        ref_to_id[spec.ref] = t.id

    # 解析 depends_on（ref → task id）
    for spec in req.tasks:
        t = db.query(TaskModel).filter(TaskModel.id == ref_to_id[spec.ref]).first()
        t.depends_on = json.dumps([ref_to_id[r] for r in spec.depends_on])

    _advance(db, job)
    db.commit()

    return {"job_id": job.id, "message": f"已派活：{req.goal}", **_job_to_dict(db, job)}


# ── 查询 ──

@router.get("/jobs")
def list_jobs(boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """列出老板名下的 job——只看自己派的活，不是全平台的（之前没按 boss_id 过滤，是 bug）。"""
    jobs = db.query(JobModel).filter(JobModel.boss_id == boss.id).order_by(JobModel.created_at.desc()).all()
    return {"total": len(jobs), "jobs": [_job_to_dict(db, j) for j in jobs]}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, agent: AgentModel = Depends(verify_agent), db: Session = Depends(get_db)):
    """从看板里删掉一个 job（连同它的 task 和协议消息）。

    只允许删终态 job（done/cancelled/failed）——还在跑的 job 里可能有锁仓资金和正在执行的 task，
    直接删会把这些悬空，必须先 cancel（会退款、发 cancel 消息）才能删。
    """
    job = _get_job_or_404(db, job_id)
    _require_boss(job, agent)
    if job.status not in (JobStatus.DONE, JobStatus.CANCELLED, JobStatus.FAILED):
        raise HTTPException(status_code=400, detail="还在进行中的 job 不能直接删除，请先「取消」再删除")
    db.query(TaskModel).filter(TaskModel.job_id == job.id).delete(synchronize_session=False)
    db.query(MessageModel).filter(MessageModel.thread_id == job.id).delete(synchronize_session=False)
    db.delete(job)
    db.commit()
    return {"job_id": job_id, "deleted": True}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(JobModel).filter(JobModel.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="job 不存在")
    return _job_to_dict(db, job)


@router.get("/tasks")
def list_my_tasks(
    status: Optional[str] = None,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    """provider 拉自己被派到的任务（取代靠 SPAWN 消息轮询/靠 transactions 轮询这种老路子）。

    给 agentfleet-skill 的 `serve` 常驻进程用：每隔几秒调一次
    `GET /api/tasks?status=assigned`，拿到就直接干活、done/fail，
    不用再去猜哪条 transaction 是自己的。
    """
    q = db.query(TaskModel).filter(TaskModel.provider_id == agent.id)
    if status:
        try:
            q = q.filter(TaskModel.status == TaskStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"未知 status: {status}")
    tasks = q.order_by(TaskModel.created_at.asc()).all()
    return {"total": len(tasks), "tasks": [_task_to_dict(db, t) for t in tasks]}


# ── 控制面（老板喊停/调整）──

def _require_boss(job: JobModel, agent: AgentModel):
    if agent.role != AgentRole.BOSS or job.boss_id != agent.id:
        raise HTTPException(status_code=403, detail="只有该 job 的老板可以控制")


def _get_job_or_404(db: Session, job_id: str) -> JobModel:
    job = db.query(JobModel).filter(JobModel.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="job 不存在")
    return job


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str, agent: AgentModel = Depends(verify_agent), db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    _require_boss(job, agent)
    if job.status not in (JobStatus.DRAFT, JobStatus.RUNNING):
        raise HTTPException(status_code=400, detail=f"当前状态 {job.status} 不可暂停")
    boss = db.query(AgentModel).filter(AgentModel.id == job.boss_id).first()

    for t in db.query(TaskModel).filter(TaskModel.job_id == job.id).all():
        if t.status in (TaskStatus.ASSIGNED, TaskStatus.RUNNING):
            p = db.query(AgentModel).filter(AgentModel.id == t.provider_id).first()
            _send_coordinator_message(db, boss, p, "pause", {"task_id": t.id}, thread_id=job.id)
            t.status = TaskStatus.PAUSED
    job.status = JobStatus.PAUSED
    job.updated_at = datetime.utcnow()
    db.commit()
    return {"job_id": job.id, "status": "paused"}


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, agent: AgentModel = Depends(verify_agent), db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    _require_boss(job, agent)
    if job.status != JobStatus.PAUSED:
        raise HTTPException(status_code=400, detail="当前非暂停状态，无需恢复")
    boss = db.query(AgentModel).filter(AgentModel.id == job.boss_id).first()

    for t in db.query(TaskModel).filter(TaskModel.job_id == job.id).all():
        if t.status == TaskStatus.PAUSED:
            p = db.query(AgentModel).filter(AgentModel.id == t.provider_id).first()
            _send_coordinator_message(db, boss, p, "resume", {"task_id": t.id}, thread_id=job.id)
            t.status = TaskStatus.RUNNING
    job.status = JobStatus.RUNNING
    job.updated_at = datetime.utcnow()
    db.commit()
    return {"job_id": job.id, "status": "running"}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, agent: AgentModel = Depends(verify_agent), db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    _require_boss(job, agent)
    if job.status in (JobStatus.DONE, JobStatus.CANCELLED, JobStatus.FAILED):
        raise HTTPException(status_code=400, detail="已终结的 job 不可取消")
    boss = db.query(AgentModel).filter(AgentModel.id == job.boss_id).first()

    for t in db.query(TaskModel).filter(TaskModel.job_id == job.id).all():
        if t.status in (TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.REJECTED):
            continue
        p = db.query(AgentModel).filter(AgentModel.id == t.provider_id).first()
        _send_coordinator_message(db, boss, p, "cancel", {"task_id": t.id}, thread_id=job.id)
        _refund_task(db, t)  # 把锁定/已交付未结算的资金退还老板（v1 只退 off-chain；链上退款后续）
        t.status = TaskStatus.CANCELLED
    job.status = JobStatus.CANCELLED
    job.updated_at = datetime.utcnow()
    db.commit()
    return {"job_id": job.id, "status": "cancelled"}


@router.post("/jobs/{job_id}/adjust")
def adjust_job(
    job_id: str,
    req: AdjustRequest,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    job = _get_job_or_404(db, job_id)
    _require_boss(job, agent)
    if job.status in (JobStatus.DONE, JobStatus.CANCELLED, JobStatus.FAILED):
        raise HTTPException(status_code=400, detail="已终结的 job 不可调整")
    boss = db.query(AgentModel).filter(AgentModel.id == job.boss_id).first()

    job.budget = req.budget
    for t in db.query(TaskModel).filter(TaskModel.job_id == job.id).all():
        if t.status in (TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.PAUSED):
            p = db.query(AgentModel).filter(AgentModel.id == t.provider_id).first()
            _send_coordinator_message(
                db, boss, p, "adjust",
                {"task_id": t.id, "budget": req.budget},
                thread_id=job.id,
            )
    job.updated_at = datetime.utcnow()
    db.commit()
    return {"job_id": job.id, "budget": req.budget, "status": "adjusted"}


# ── 任务完成 / 失败（provider 侧）──

@router.post("/tasks/{task_id}/done")
def task_done(
    task_id: str,
    req: TaskDoneRequest,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    """provider 交付：写入 DELIVERED（不再是交付即放款）。
    require_review=False 且通过结构校验 → 立即自动验收结算；否则停在 DELIVERED，等老板 /accept 或 /reject。
    """
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="task 不存在")
    if task.provider_id != agent.id:
        raise HTTPException(status_code=403, detail="只有该 task 的 provider 可以交付")

    if task.status in (TaskStatus.DONE, TaskStatus.DELIVERED):
        return {"task_id": task.id, "status": task.status.value}

    task.output_cid = req.cid
    task.output_json = json.dumps(req.output, ensure_ascii=False)
    task.status = TaskStatus.DELIVERED
    task.updated_at = datetime.utcnow()

    # 真实结算：拿交付物实际内容数 token，按全平台统一费率算出这次真该付多少——
    # 不是派单时锁的那个预授权上限。封顶在 task.price（锁仓额），多退（锁多了）少不补
    # （provider 交得少不会多收，防止"随便交个短的"这种薅法）。
    raw_price, tokens = settle_price(req.output)
    task.token_count = tokens
    task.settled_price = min(raw_price, task.price)

    job = db.query(JobModel).filter(JobModel.id == task.job_id).first()

    auto_accepted = False
    if not task.require_review and _auto_accept_check(task):
        _accept_task(db, job, task)
        auto_accepted = True

    # 先落库（本 task 状态 + 结算)再推进 DAG：并行任务几乎同时完成时，
    # 若不先 commit，最后一个请求的 _advance 会读到其它任务未提交的旧状态，
    # 导致串行依赖任务永远不被派发。commit 后依赖彼此可见，最后完成的请求可靠派发后续任务。
    db.commit()

    if auto_accepted:
        _advance(db, job)
        db.commit()

    return {
        "task_id": task.id,
        "status": task.status.value,
        "cid": req.cid,
        "auto_accepted": auto_accepted,
    }


@router.post("/tasks/{task_id}/accept")
def accept_task(
    task_id: str,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    """老板人工验收通过：结算 + 推进 DAG（require_review=True 的 task 走这里）。"""
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="task 不存在")
    job = db.query(JobModel).filter(JobModel.id == task.job_id).first()
    if not job or agent.role != AgentRole.BOSS or job.boss_id != agent.id:
        raise HTTPException(status_code=403, detail="只有该 job 的老板可以验收")
    if task.status != TaskStatus.DELIVERED:
        raise HTTPException(status_code=400, detail=f"task 当前状态 {task.status.value} 不可验收")

    _accept_task(db, job, task)
    db.commit()
    _advance(db, job)
    db.commit()
    return {"task_id": task.id, "status": "done"}


@router.post("/tasks/{task_id}/reject")
def reject_task(
    task_id: str,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    """老板拒绝验收：退款 + task=REJECTED（不结算给 provider）。"""
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="task 不存在")
    job = db.query(JobModel).filter(JobModel.id == task.job_id).first()
    if not job or agent.role != AgentRole.BOSS or job.boss_id != agent.id:
        raise HTTPException(status_code=403, detail="只有该 job 的老板可以拒绝验收")
    if task.status != TaskStatus.DELIVERED:
        raise HTTPException(status_code=400, detail=f"task 当前状态 {task.status.value} 不可拒绝")

    _refund_task(db, task)
    task.status = TaskStatus.REJECTED
    task.updated_at = datetime.utcnow()
    db.commit()
    _advance(db, job)
    db.commit()
    return {"task_id": task.id, "status": "rejected"}


@router.post("/tasks/{task_id}/fail")
def task_fail(
    task_id: str,
    agent: AgentModel = Depends(verify_agent),
    db: Session = Depends(get_db),
):
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="task 不存在")
    if task.provider_id != agent.id:
        raise HTTPException(status_code=403, detail="只有该 task 的 provider 可以标记失败")

    _refund_task(db, task)  # 修复：原先失败后锁仓资金会卡死，不退款
    task.status = TaskStatus.FAILED
    task.updated_at = datetime.utcnow()
    job = db.query(JobModel).filter(JobModel.id == task.job_id).first()
    _advance(db, job)
    db.commit()
    return {"task_id": task.id, "status": "failed"}
