"""
技能包 API —— 方法论层，独立于 service_name 身份 / MCP 连接器
================================================================
- POST   /api/skills                       老板创建一个技能包（自己的库，boss_id 限定）
- GET    /api/skills                       列出老板自己的技能库
- PUT    /api/skills/{skill_id}            改名字/正文
- DELETE /api/skills/{skill_id}            删（连同所有 agent 身上的装配关系一起清）
- GET    /api/skills/agents/{agent_id}     查这个 agent 装了哪些技能包
- POST   /api/skills/agents/{agent_id}/install    装配一个技能包到这个 agent（body: skill_id）
- DELETE /api/skills/agents/{agent_id}/install/{skill_id}   卸载

技能包内容怎么真正生效见 backend/models.py 里 SkillModel 上面那段注释——这里只负责库的
增删改查和装配关系，不管 worker 端怎么消费。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from backend.database import get_db
from backend.models import AgentModel, SkillModel, AgentSkillModel
from backend.auth import verify_boss

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000, description="说明书：干什么用的/什么场景该用，不是正文")
    content: str = Field(default="", max_length=20000, description="正文：具体怎么做，例子也写在这里面")
    local_path: str = Field(
        default="", max_length=1024,
        description="可选：你自己电脑上已经装好的技能文件夹路径，跟 content 二选一或并存——"
                     "填了这个，worker（agentfleet-skill serve）会把它当 --add-dir 挂给 claude/codex",
    )


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    local_path: Optional[str] = None


class InstallRequest(BaseModel):
    skill_id: str = Field(..., min_length=1)


def _skill_to_dict(s: SkillModel) -> dict:
    return {
        "skill_id": s.id,
        "name": s.name,
        "description": s.description,
        "content": s.content,
        "local_path": s.local_path,
        "created_at": s.created_at.isoformat() if s.created_at else "",
        "updated_at": s.updated_at.isoformat() if s.updated_at else "",
    }


def _get_own_skill_or_404(db: Session, boss: AgentModel, skill_id: str) -> SkillModel:
    s = db.query(SkillModel).filter(SkillModel.id == skill_id, SkillModel.boss_id == boss.id).first()
    if not s:
        raise HTTPException(status_code=404, detail="技能包不存在或不属于你")
    return s


def _get_own_agent_or_404(db: Session, boss: AgentModel, agent_id: str) -> AgentModel:
    a = db.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.boss_id == boss.id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Agent 不存在或不属于你")
    return a


# ── 技能库 CRUD ──

@router.post("")
def create_skill(req: SkillCreateRequest, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    skill = SkillModel(
        boss_id=boss.id, name=req.name, description=req.description or "",
        content=req.content or "", local_path=req.local_path or "",
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return {"message": f"已创建技能包：{skill.name}", **_skill_to_dict(skill)}


@router.get("")
def list_skills(boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """老板自己的技能库——只看自己的，不是全平台的（跟 agent/job 的 boss_id 隔离是同一套规则）。"""
    skills = db.query(SkillModel).filter(SkillModel.boss_id == boss.id).order_by(SkillModel.created_at.desc()).all()
    return {"total": len(skills), "skills": [_skill_to_dict(s) for s in skills]}


@router.put("/{skill_id}")
def update_skill(skill_id: str, req: SkillUpdateRequest, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    skill = _get_own_skill_or_404(db, boss, skill_id)
    if req.name is not None:
        skill.name = req.name
    if req.description is not None:
        skill.description = req.description
    if req.content is not None:
        skill.content = req.content
    if req.local_path is not None:
        skill.local_path = req.local_path
    db.commit()
    db.refresh(skill)
    return {"message": "技能包已更新", **_skill_to_dict(skill)}


@router.delete("/{skill_id}")
def delete_skill(skill_id: str, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """删掉一个技能包——顺带把它在所有 agent 身上的装配关系也清掉，不留悬空引用。"""
    skill = _get_own_skill_or_404(db, boss, skill_id)
    name = skill.name
    n = db.query(AgentSkillModel).filter(AgentSkillModel.skill_id == skill_id).delete(synchronize_session=False)
    db.delete(skill)
    db.commit()
    return {"message": f"已删除技能包：{name}（同时从 {n} 个 agent 身上卸载）"}


# ── agent 的装配关系 ──

@router.get("/agents/{agent_id}")
def list_agent_skills(agent_id: str, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    agent = _get_own_agent_or_404(db, boss, agent_id)
    rows = (
        db.query(SkillModel)
        .join(AgentSkillModel, AgentSkillModel.skill_id == SkillModel.id)
        .filter(AgentSkillModel.agent_id == agent.id)
        .order_by(SkillModel.created_at.desc())
        .all()
    )
    return {"agent_id": agent.id, "total": len(rows), "skills": [_skill_to_dict(s) for s in rows]}


@router.post("/agents/{agent_id}/install")
def install_skill(agent_id: str, req: InstallRequest, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    agent = _get_own_agent_or_404(db, boss, agent_id)
    skill = _get_own_skill_or_404(db, boss, req.skill_id)

    existing = db.query(AgentSkillModel).filter(
        AgentSkillModel.agent_id == agent.id, AgentSkillModel.skill_id == skill.id,
    ).first()
    if existing:
        return {"message": f"「{skill.name}」已经装在「{agent.name}」身上了，不用重复装配"}

    db.add(AgentSkillModel(agent_id=agent.id, skill_id=skill.id))
    db.commit()
    return {"message": f"已把技能包「{skill.name}」装配到「{agent.name}」"}


@router.delete("/agents/{agent_id}/install/{skill_id}")
def uninstall_skill(agent_id: str, skill_id: str, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    agent = _get_own_agent_or_404(db, boss, agent_id)
    skill = _get_own_skill_or_404(db, boss, skill_id)

    n = db.query(AgentSkillModel).filter(
        AgentSkillModel.agent_id == agent.id, AgentSkillModel.skill_id == skill.id,
    ).delete(synchronize_session=False)
    db.commit()
    if n == 0:
        return {"message": f"「{skill.name}」本来就没装在「{agent.name}」身上"}
    return {"message": f"已从「{agent.name}」卸载技能包「{skill.name}」"}
