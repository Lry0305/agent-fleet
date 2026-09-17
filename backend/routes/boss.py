"""
老板控制台 — 老板登录 / 创建 Agent / 充值（支付宝 / 微信 / 银行卡）
====================================================================
一人公司：老板（人）是唯一登录方。老板登录自己的账号 → 创建名下 agent →
充值 → 查询余额。agent 的 api_key 是给 worker 运行时用的内部凭证，不在控制台登录。

充值走「渠道抽象」：支付宝走真实签名的官方沙箱网关（backend/payments.py），没配沙箱凭证时
自动降级成标了 is_sandbox_real=False 的占位 mock；微信/银行卡目前仍是占位 mock（见该模块 docstring）。
POST /recharge 建 pending 订单；支付宝走 POST /recharge/alipay/notify 异步通知验签入账，
其余渠道保留 POST /recharge/{id}/confirm 手动模拟成功回调。
"""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.database import get_db, SessionLocal
from backend.models import (
    AgentModel, AgentRole, AuthMethod, RechargeOrderModel, RechargeStatus,
    TaskModel, TaskStatus,
)
from backend.auth import verify_agent, verify_boss
from backend.config import (
    CNY_TO_CREDIT_RATE, PAYMENT_CHANNELS, DEFAULT_CREDITS,
    DEMO_BOSS_PHONE, DEMO_BOSS_PASSWORD, DEMO_BOSS_NAME,
)
from backend.chain import _get_w3, chain_available
from backend.routes.agents import _fund_wallet, _build_manifest, agent_to_public, WORKSPACE_ROOT
from backend.payments import build_page_pay_url, alipay_configured, verify_notify
from eth_account import Account

router = APIRouter(prefix="/api/boss", tags=["boss"])


# ── 请求模型 ──

class BossRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    phone: str = Field(..., min_length=5, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)


class BossLoginRequest(BaseModel):
    phone: str = Field(..., min_length=5, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)


class BossChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    role: str = Field(default="provider", pattern="^(provider|consumer)$")
    service_name: Optional[str] = Field(default="")
    service_description: Optional[str] = Field(default="")
    avatar: Optional[str] = Field(default="🤖")
    avatar_color: Optional[str] = Field(default="#3370ff", description="头像配色，跟 avatar 成套的预设组合")
    description: Optional[str] = Field(default="")
    workspace_mode: str = Field(
        default="isolated", pattern="^(isolated|shared_pool)$",
        description="isolated=独立工作空间（默认）；shared_pool=额外挂一份本老板名下的共享参考知识目录",
    )
    # 认证方式三选一：api_key（默认，配合 agentfleet-skill/自己的脚本）/
    # feishu_app（真实飞书 App ID+Secret）/ openclaw_bot（真实 OpenClaw Bot Token，填进 app_id）。
    # 不管选哪种，都还是会生成一把 api_key 当备用凭证——跟 POST /api/agents/register 自注册路径行为一致，
    # 这样 agentfleet-skill serve 这类 worker 进程始终能直接用这把 key，不用依赖飞书/OpenClaw 凭证本身。
    auth_method: str = Field(default="api_key", pattern="^(api_key|feishu_app|openclaw_bot)$")
    app_id: Optional[str] = Field(default="", description="feishu_app: 飞书 App ID；openclaw_bot: Bot Token")
    app_secret: Optional[str] = Field(default="", description="feishu_app: 飞书 App Secret；openclaw_bot 可留空")
    # 注意：这里没有 price_per_call，也没有 initial_budget —— 价格不是老板定的（全平台
    # 统一费率，backend/config.py 的 GLOBAL_RATE_PER_1K），新 agent 也不凭空自带钱：一律从
    # 0 credits 起步。老板能不能估准一个任务要花多少 token 都是两说，更别说该给新 agent
    # 划多少启动资金——与其让老板猜一个数字，不如干脆不留这个口子。


class TestFeishuAppRequest(BaseModel):
    app_id: str = Field(..., min_length=1)
    app_secret: str = Field(..., min_length=1)


class UpdateAgentRequest(BaseModel):
    name: Optional[str] = None
    service_name: Optional[str] = None
    service_description: Optional[str] = None
    is_service_active: Optional[bool] = None
    avatar: Optional[str] = None
    description: Optional[str] = None
    # 同上：price_per_call 不在这里，老板改不了 agent 的价格，只能开关它的服务（is_service_active）。


class RechargeRequest(BaseModel):
    channel: str = Field(default="alipay", pattern="^(alipay|wechat|card)$")
    amount_cny: float = Field(..., gt=0, description="充值金额（元）")


class ImportAgentSpec(BaseModel):
    """导入一个已经存在的 agent：你自己另外部署的、或者别的项目里已经跑起来的。
    和创建新 agent 的区别——price_per_call 在这里必填，因为这是它自己已经声明过的报价
    （来自它自己的 manifest/配置），不是老板现场填的；导入动作只是把这份自报价登记进来。
    """
    name: str = Field(..., min_length=1, max_length=128)
    service_name: str = Field(..., min_length=1)
    service_description: Optional[str] = Field(default="")
    service_endpoint: Optional[str] = Field(default="", description="外部 agent 的服务地址，仅作记录/以后接实时调用用")
    price_per_call: float = Field(..., gt=0, description="它自己已经声明过的报价，不是老板填的")
    avatar: Optional[str] = Field(default="🤖")
    description: Optional[str] = Field(default="")
    workspace_mode: str = Field(default="isolated", pattern="^(isolated|shared_pool)$")


class ImportAgentsRequest(BaseModel):
    agents: list[ImportAgentSpec] = Field(..., min_length=1, description="批量导入；单个 agent 传长度为 1 的列表即可")


# ── 工具 ──

def _make_wallet_identity() -> tuple:
    """生成 ETH 钱包（demo 简化，明文存私钥）。返回 (eth_address, encrypted_private_key)。"""
    wallet = Account.create()
    return wallet.address, wallet.key.hex()


def _fund_if_possible(eth_address: str, role_label: str):
    """给新钱包预充 ETH（provider 要 gas、boss 要锁仓）。失败不阻塞。"""
    if chain_available():
        try:
            w3 = _get_w3()
            if w3 and _fund_wallet(w3, eth_address, DEFAULT_CREDITS * 0.01):
                print(f"[Boss] ✅ {role_label} 钱包预充 ETH: {eth_address}")
        except Exception as e:
            print(f"[Boss] ⚠️ 钱包预充失败: {e}")


def _boss_to_public(agent: AgentModel, db: Session = None) -> dict:
    """agent 的对外展示字段。settled_count / settled_total 是这个 provider 真实交付、
    验收通过、结算过的次数和累计金额——直接查 TaskModel（真实发生过的结算记录），
    不是拿 total_earned 展示（新 agent 现在一律 0 起步，没有启动资金这个口子了；但老数据
    里仍可能有早期版本留下的非零 total_earned，那是给的钱，不是它自己干活赚的钱，
    两者不该算在一起显示成"业绩"）。
    """
    settled_count, settled_total = 0, 0.0
    if db is not None and agent.role == AgentRole.PROVIDER:
        row = db.query(
            func.count(TaskModel.id), func.coalesce(func.sum(TaskModel.settled_price), 0.0)
        ).filter(
            TaskModel.provider_id == agent.id,
            TaskModel.status == TaskStatus.DONE,
            TaskModel.settled_price > 0,
        ).first()
        settled_count, settled_total = int(row[0] or 0), round(float(row[1] or 0.0), 2)
    return {
        **agent_to_public(agent),
        "phone": agent.phone or "",
        "credit_balance": agent.total_earned - agent.total_spent,
        "settled_count": settled_count,
        "settled_total": settled_total,
    }


def _provision_workspace(agent: AgentModel, workspace_mode: str = "isolated") -> str:
    """把每个 agent 开成一个硅基生命：出生即分配自己的 edge 空间
    （manifest 快照 + 私有 memory.json，平台不没收，只有 agent 自己的 api_key 能读写）。
    shared_pool 模式额外挂一个本老板名下的共享参考知识目录，不影响它自己的私有 memory。
    返回共享目录的相对路径（isolated 模式返回空字符串）。
    """
    ws_dir = WORKSPACE_ROOT / agent.id
    (ws_dir / "state").mkdir(parents=True, exist_ok=True)
    (ws_dir / "manifest.json").write_text(
        json.dumps(agent.get_manifest(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    memory_path = ws_dir / "memory.json"
    if not memory_path.exists():
        memory_path.write_text(
            json.dumps({"agent_id": agent.id, "notes": []}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    shared_ref = ""
    if workspace_mode == "shared_pool" and agent.boss_id:
        shared_dir = WORKSPACE_ROOT / "_shared" / agent.boss_id
        shared_dir.mkdir(parents=True, exist_ok=True)
        shared_ref = f"agent_workspaces/_shared/{agent.boss_id}"
    return shared_ref


def _create_agent_row(db: Session, *, name, role, boss_id, service_name, service_description,
                      price_per_call, avatar, description, phone=None, password=None,
                      workspace_mode="isolated", auth_method="api_key",
                      app_id=None, app_secret_hash=None, avatar_color="#3370ff",
                      starting_credits: float = 0.0) -> AgentModel:
    """内部：建一个 agent/boss 行（钱包 + api_key + 初始额度 + manifest + 独立工作空间）。

    api_key 永远都会生成——即使 auth_method 选的是 feishu_app/openclaw_bot 也一样留一把当备用凭证
    （跟 POST /api/agents/register 自注册路径的行为一致），这样不管选哪种认证方式，
    agentfleet-skill serve 这类 worker 进程都能直接拿这把 key 用。

    starting_credits 默认 0——不是每个 agent 天生自带钱。只有「老板本人注册」这个入口
    （真人/公司开户）才该给注册福利，调用方显式传 DEFAULT_CREDITS；老板名下新建的
    provider/consumer agent 不该凭空多出一笔钱，那是铸币，不是从老板余额里真实划过去的。
    """
    eth_address, priv = _make_wallet_identity()
    api_key = AgentModel.generate_api_key()

    agent = AgentModel(
        name=name,
        role=AgentRole(role),
        boss_id=boss_id,
        auth_method=AuthMethod(auth_method),
        api_key=api_key,
        secret_hash=AgentModel.hash_secret(api_key),
        app_id=app_id,
        app_secret_hash=app_secret_hash,
        phone=phone,
        password_hash=AgentModel.hash_secret(password) if password else None,
        description=description or "",
        avatar=avatar or "🤖",
        avatar_color=avatar_color or "#3370ff",
        platform="web",
        total_earned=starting_credits,
        credit_balance=starting_credits,
        service_name=service_name or "",
        service_description=service_description or "",
        price_per_call=price_per_call or 0.0,
        eth_address=eth_address,
        encrypted_private_key=priv,
        workspace_mode=workspace_mode,
    )
    agent.set_manifest(_build_manifest(agent))
    db.add(agent)
    db.commit()
    db.refresh(agent)
    _fund_if_possible(eth_address, role)
    _provision_workspace(agent, workspace_mode)
    return agent


# ── 老板注册 / 登录 ──

@router.post("/register")
def register_boss(req: BossRegisterRequest, db: Session = Depends(get_db)):
    """老板注册（手机号 + 密码）。返回 api_key 供前端后续请求使用。"""
    if db.query(AgentModel).filter(AgentModel.phone == req.phone, AgentModel.role == AgentRole.BOSS).first():
        raise HTTPException(status_code=409, detail="该手机号已注册")

    agent = _create_agent_row(
        db, name=req.name, role="boss", boss_id=None,
        service_name="", service_description="", price_per_call=0.0,
        avatar="👔", description="老板（人）", phone=req.phone, password=req.password,
        starting_credits=DEFAULT_CREDITS,  # 真人/公司开户注册福利，这里是唯一该给的地方
    )
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "role": "boss",
        "phone": agent.phone,
        "api_key": agent.api_key,
        "credit_balance": agent.total_earned - agent.total_spent,
        "message": f"老板 {agent.name} 注册成功",
    }


@router.post("/login")
def login_boss(req: BossLoginRequest, db: Session = Depends(get_db)):
    """老板登录（手机号 + 密码）。返回 api_key。"""
    sh = AgentModel.hash_secret(req.password)
    boss = db.query(AgentModel).filter(
        AgentModel.role == AgentRole.BOSS,
        AgentModel.phone == req.phone,
        AgentModel.password_hash == sh,
    ).first()
    if not boss:
        raise HTTPException(status_code=401, detail="手机号或密码错误")

    return {
        "agent_id": boss.id,
        "name": boss.name,
        "role": "boss",
        "phone": boss.phone or "",
        "api_key": boss.api_key,
        "avatar": boss.avatar,
        "credit_balance": boss.total_earned - boss.total_spent,
        "total_earned": boss.total_earned,
        "total_spent": boss.total_spent,
        "eth_address": boss.eth_address,
        "did": boss.did,
        "message": f"欢迎回来，老板 {boss.name}！",
    }


@router.post("/change-password")
def change_password(req: BossChangePasswordRequest, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """老板改自己的登录密码——要先对一遍旧密码，不是拿着 api_key 就能直接改。"""
    if not boss.password_hash or AgentModel.hash_secret(req.old_password) != boss.password_hash:
        raise HTTPException(status_code=401, detail="旧密码不对")
    boss.password_hash = AgentModel.hash_secret(req.new_password)
    boss.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "密码已修改"}


# ── 老板创建 / 管理名下 agent ──

@router.post("/agents/test-feishu-app")
def test_feishu_app(req: TestFeishuAppRequest, boss: AgentModel = Depends(verify_boss)):
    """真打两次飞书开放平台接口——不是只验证 App ID/Secret 是不是真实的一对就完事。第一步
    tenant_access_token/internal 只能证明这一对凭证飞书那边确实存在，换不来"这真的是个能被
    调用的 Bot"这个结论（普通应用一样能拿到 token）；第二步拿这个 token 去打 bot/v3/info，
    只有真的挂了机器人能力的应用才会成功——两步都过，才算真正连上了一个可调用的飞书 agent，
    不是只测了凭证对不对。

    纯只读：没有 db 依赖（看函数签名），只转发两次飞书开放平台请求，不会往数据库里写任何
    东西——测多少次都不会把这个 App ID 注册成 agent。真正建号只发生在下面的 POST /agents。
    """
    import requests as _requests
    try:
        resp = _requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": req.app_id, "app_secret": req.app_secret},
            timeout=8,
        )
        data = resp.json()
    except Exception as e:
        return {"ok": False, "message": f"连不上飞书开放平台：{e}"}
    if data.get("code") != 0:
        return {
            "ok": False,
            "message": f"❌ 飞书返回错误：{data.get('msg', '未知错误')}（code={data.get('code')}）——"
                       f"检查 App ID / Secret 是不是复制全了、有没有多余空格或换行",
        }
    token = data.get("tenant_access_token", "")

    try:
        bot_resp = _requests.get(
            "https://open.feishu.cn/open-apis/bot/v3/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        bot_data = bot_resp.json()
    except Exception as e:
        return {"ok": False, "message": f"拿到 token 了，但调 bot/v3/info 失败：{e}"}
    if bot_data.get("code") != 0:
        return {
            "ok": False,
            "message": f"❌ App ID / Secret 是真的，但这个应用不是可调用的 Bot（{bot_data.get('msg', '未知错误')}）"
                       f"——去飞书开放平台后台确认这个应用开了机器人能力",
        }
    return {"ok": True, "message": "✅ 验证通过"}


@router.post("/agents")
def create_agent(req: CreateAgentRequest, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """老板创建名下的 agent（provider / consumer）。返回 api_key 供 worker 运行时使用。

    同名去重：一个老板名下不允许出现两个同名 agent（避免重复建号）。
    价格不在这里定，也不用任何人定：全平台统一费率（GLOBAL_RATE_PER_1K），新 agent
    声明 service_name（skill）之后就能被派活，不需要再单独挂牌价。

    认证方式选 feishu_app/openclaw_bot 时，这里存的是真实凭证（App ID+Secret / Bot Token）——
    但光填这个还不会让它变成一个真的在监听消息的机器人：还需要一个真实进程（比如
    agentfleet-skill serve，或你自己的脚本）拿着下面返回的 api_key 去轮询 /api/tasks 干活。

    余额：新 agent 一律从 0 credits 起步，不凭空自带钱，也没有可选的启动资金划拨——
    只有 role="boss" 的真人/公司开户才有注册福利。
    """
    existing = db.query(AgentModel).filter(
        AgentModel.boss_id == boss.id, AgentModel.name == req.name
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"你名下已有同名 agent：{req.name}（id={existing.id}）。换个名字，或者先处理/删除旧的",
        )

    auth_method = req.auth_method
    app_id, app_secret_hash = None, None
    if auth_method == "feishu_app":
        if not req.app_id or not req.app_secret:
            raise HTTPException(status_code=400, detail="飞书认证需要提供 App ID 和 App Secret")
        app_id, app_secret_hash = req.app_id, AgentModel.hash_secret(req.app_secret)
    elif auth_method == "openclaw_bot":
        if not req.app_id:
            raise HTTPException(status_code=400, detail="OpenClaw 认证需要提供 Bot Token")
        app_id = req.app_id
        app_secret_hash = AgentModel.hash_secret(req.app_secret) if req.app_secret else AgentModel.hash_secret(req.app_id)

    existing_by_app_id = db.query(AgentModel).filter(AgentModel.app_id == app_id).first() if app_id else None
    if existing_by_app_id:
        # 报清楚是「哪个 agent」占用了这个 App ID/Bot Token——不然容易被误以为是「测试连接」
        # 把它注册进去了（其实 /agents/test-feishu-app 没有 db 依赖，读不了也写不了库，只是
        # 纯粹转发两次飞书开放平台请求；真正占位的是之前已经用这个 App ID 建成功的另一个 agent）。
        raise HTTPException(
            status_code=409,
            detail=f"这个 App ID / Bot Token 已经被你名下的 agent「{existing_by_app_id.name}」"
                   f"（id={existing_by_app_id.id}）注册过了——一个真实 App ID 只能绑一个 agent，"
                   f"换一个 App ID，或者先去「我的 Agent」把「{existing_by_app_id.name}」改名/处理掉再建新的",
        )

    agent = _create_agent_row(
        db, name=req.name, role=req.role, boss_id=boss.id,
        service_name=req.service_name or "", service_description=req.service_description or "",
        price_per_call=0.0, avatar=req.avatar, avatar_color=req.avatar_color, description=req.description or "",
        workspace_mode=req.workspace_mode,
        auth_method=auth_method, app_id=app_id, app_secret_hash=app_secret_hash,
    )

    return {
        "agent": {**_boss_to_public(agent, db), "boss_id": boss.id},
        "api_key": agent.api_key,
        # 一次性返回私钥：worker 运行时用于消息签名（demo 简化；生产由 agent 自己生成、平台只存公钥）
        "private_key": agent.encrypted_private_key,
        "message": f"已创建 {req.role} agent：{agent.name}（统一费率计价，声明 service_name 后即可被派活；起始余额 0）",
    }


@router.post("/agents/{agent_id}/rotate-key")
def rotate_agent_key(agent_id: str, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """重置某个 agent 的 api_key——旧 key 立即失效，返回新 key（仅此一次展示）。

    用在 key 意外泄露之后（比如截图、聊天记录、日志里带出了明文 key），不用把整个 agent 销毁重建。
    """
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.boss_id == boss.id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在或不属于你")
    new_key = AgentModel.generate_api_key()
    agent.api_key = new_key
    agent.secret_hash = AgentModel.hash_secret(new_key)
    agent.updated_at = datetime.utcnow()
    db.commit()
    return {"agent_id": agent_id, "api_key": new_key, "message": "已重置，旧 key 立即失效——正在跑的 worker 进程要换成新 key 才能继续认证"}


@router.post("/agents/import")
def import_agents(req: ImportAgentsRequest, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """批量导入已经存在的 agent（你自己另外部署的、或者其它项目里已经跑起来的）。

    同名或同 service_endpoint（非空时）已存在则更新那一条而不是重复建号；
    price_per_call 必填，因为这是导入对象自己已经声明过的报价，不是老板现场定的。
    """
    results = []
    for spec in req.agents:
        existing = db.query(AgentModel).filter(
            AgentModel.boss_id == boss.id,
            (AgentModel.name == spec.name) | (
                (AgentModel.service_endpoint != "") & (AgentModel.service_endpoint == (spec.service_endpoint or "\0"))
            ),
        ).first()
        if existing:
            existing.service_name = spec.service_name
            existing.service_description = spec.service_description or existing.service_description
            existing.service_endpoint = spec.service_endpoint or existing.service_endpoint
            existing.price_per_call = spec.price_per_call
            existing.avatar = spec.avatar or existing.avatar
            existing.description = spec.description or existing.description
            existing.workspace_mode = spec.workspace_mode
            existing.set_manifest(_build_manifest(existing))
            existing.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            _provision_workspace(existing, spec.workspace_mode)
            results.append({"action": "updated", **_boss_to_public(existing, db)})
            continue

        agent = _create_agent_row(
            db, name=spec.name, role="provider", boss_id=boss.id,
            service_name=spec.service_name, service_description=spec.service_description or "",
            price_per_call=spec.price_per_call, avatar=spec.avatar, description=spec.description or "",
            workspace_mode=spec.workspace_mode,
        )
        if spec.service_endpoint:
            agent.service_endpoint = spec.service_endpoint
            agent.set_manifest(_build_manifest(agent))
            db.commit()
            db.refresh(agent)
        results.append({"action": "created", **_boss_to_public(agent, db)})

    return {"total": len(results), "agents": results}


@router.get("/agents")
def list_agents(boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """列出老板名下的所有 agent。"""
    agents = db.query(AgentModel).filter(AgentModel.boss_id == boss.id).order_by(
        AgentModel.created_at.desc()
    ).all()
    return {
        "total": len(agents),
        "agents": [{**_boss_to_public(a, db), "boss_id": boss.id} for a in agents],
    }


@router.put("/agents/{agent_id}")
def update_agent(agent_id: str, req: UpdateAgentRequest, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """老板改名下 agent（改名 / 改价 / 开关服务）。"""
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.boss_id == boss.id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在或不属于你")

    for field, value in req.model_dump(exclude_none=True).items():
        setattr(agent, field, value)
    agent.set_manifest(_build_manifest(agent))
    agent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(agent)
    _provision_workspace(agent, agent.workspace_mode or "isolated")
    return {"message": "更新成功", "agent": {**_boss_to_public(agent, db), "boss_id": boss.id}}


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """老板销毁名下 agent（创建/销毁 = 进化体系最小闭环）。

    销毁前先清理它手上还没走完的 task（ASSIGNED/RUNNING/PAUSED/DELIVERED）：锁的钱退回
    老板、task 标 CANCELLED、所属 job 重新跑一遍推进。不这么做的话 task 会永远卡在那几个
    状态——provider 已经不存在了，谁都不会再交付；这个 job 也没法再 pause/resume/cancel
    （之前这几个控制端点碰到 provider 是 None 会直接 500），更没法删（delete_job 要求终态），
    锁住的钱就这么废在半空——是真实发生过的 bug，不是假设。
    """
    agent = db.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.boss_id == boss.id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在或不属于你")
    name = agent.name

    from backend.models import JobModel
    from backend.routes.jobs import _refund_task, _advance

    open_tasks = db.query(TaskModel).filter(
        TaskModel.provider_id == agent.id,
        TaskModel.status.in_([
            TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.DELIVERED,
        ]),
    ).all()
    affected_job_ids = set()
    for t in open_tasks:
        _refund_task(db, t)  # 把这个 task 锁的钱退回老板
        t.status = TaskStatus.CANCELLED
        t.updated_at = datetime.utcnow()
        affected_job_ids.add(t.job_id)

    db.delete(agent)
    db.commit()

    for jid in affected_job_ids:
        job = db.query(JobModel).filter(JobModel.id == jid).first()
        if job:
            _advance(db, job)  # 受影响的 job 重新判一次状态（可能因此变成 FAILED/CANCELLED）
    db.commit()

    note = f"，已自动取消它手上 {len(open_tasks)} 个未完成任务并退款" if open_tasks else ""
    return {"message": f"已销毁 agent：{name}{note}"}


# ── 充值（渠道抽象 + mock 网关）──

@router.post("/recharge")
def create_recharge(req: RechargeRequest, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """建充值订单（pending）。支付宝渠道且配了沙箱凭证时返回真实签名的沙箱支付链接
    （is_real_gateway=True）；否则返回标注清楚的占位 mock 链接（is_real_gateway=False）。
    """
    if req.channel not in PAYMENT_CHANNELS:
        raise HTTPException(status_code=400, detail="不支持的支付渠道")
    amount_credits = round(req.amount_cny * CNY_TO_CREDIT_RATE, 2)

    order = RechargeOrderModel(
        boss_id=boss.id,
        channel=req.channel,
        amount_cny=req.amount_cny,
        amount_credits=amount_credits,
        status=RechargeStatus.PENDING,
        pay_url="",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    if req.channel == "alipay":
        order.pay_url = build_page_pay_url(order.id, req.amount_cny, f"AgentFleet 充值 {req.amount_cny} 元")
        order.is_sandbox_real = alipay_configured()
    else:
        order.pay_url = f"mockpay://{req.channel}?order={order.id}&amount={req.amount_cny}"
        order.is_sandbox_real = False
    db.commit()
    db.refresh(order)

    return {
        "order_id": order.id,
        "channel": order.channel,
        "channel_label": PAYMENT_CHANNELS[order.channel],
        "amount_cny": order.amount_cny,
        "amount_credits": order.amount_credits,
        "status": order.status.value,
        "pay_url": order.pay_url,
        "is_real_gateway": order.is_sandbox_real,
    }


@router.post("/recharge/alipay/notify")
async def alipay_notify(request: Request, db: Session = Depends(get_db)):
    """支付宝异步通知（notify_url 回调）：验签通过 + 交易成功才真正入账。
    未配沙箱凭证时 verify_notify 恒为 False，这个端点等于自动关闭，不会被拿来伪造入账。
    支付宝要求收到就回文本 "success"，否则它会重试；验签失败按官方约定回 "fail"。
    """
    form = dict((await request.form()))
    if not verify_notify(form):
        return Response(content="fail", media_type="text/plain")

    order = db.query(RechargeOrderModel).filter(RechargeOrderModel.id == form.get("out_trade_no")).first()
    if not order or order.status == RechargeStatus.PAID:
        return Response(content="success", media_type="text/plain")
    if form.get("trade_status") not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return Response(content="success", media_type="text/plain")

    boss = db.query(AgentModel).filter(AgentModel.id == order.boss_id).first()
    order.status = RechargeStatus.PAID
    order.paid_at = datetime.utcnow()
    if boss:
        boss.total_earned += order.amount_credits
        boss.updated_at = datetime.utcnow()
    db.commit()
    return Response(content="success", media_type="text/plain")


@router.post("/recharge/{order_id}/confirm")
def confirm_recharge(order_id: str, boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """mock 支付成功回调：订单置 paid，老板入账。生产环境换成支付宝/微信异步通知。"""
    order = db.query(RechargeOrderModel).filter(
        RechargeOrderModel.id == order_id, RechargeOrderModel.boss_id == boss.id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="充值订单不存在")
    if order.status == RechargeStatus.PAID:
        raise HTTPException(status_code=400, detail="订单已支付")

    order.status = RechargeStatus.PAID
    order.paid_at = datetime.utcnow()
    boss.total_earned += order.amount_credits  # 复用余额公式 total_earned - total_spent
    boss.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(order)

    return {
        "message": f"充值成功 +{order.amount_credits} credits",
        "order_id": order.id,
        "channel": order.channel,
        "amount_cny": order.amount_cny,
        "amount_credits": order.amount_credits,
        "new_balance": boss.total_earned - boss.total_spent,
    }


@router.get("/recharge/orders")
def list_recharge_orders(boss: AgentModel = Depends(verify_boss), db: Session = Depends(get_db)):
    """老板的充值订单流水（可视化用）。"""
    orders = db.query(RechargeOrderModel).filter(RechargeOrderModel.boss_id == boss.id).order_by(
        RechargeOrderModel.created_at.desc()
    ).all()
    return {
        "total": len(orders),
        "orders": [{
            "order_id": o.id,
            "channel": o.channel,
            "channel_label": PAYMENT_CHANNELS.get(o.channel, o.channel),
            "amount_cny": o.amount_cny,
            "amount_credits": o.amount_credits,
            "status": o.status.value,
            "created_at": o.created_at.isoformat() if o.created_at else "",
            "paid_at": o.paid_at.isoformat() if o.paid_at else "",
        } for o in orders],
    }


# ── 演示老板播种（main.py startup 调用）──

def ensure_demo_boss():
    """确保存在一个演示老板账号（手机号 + 密码），供 UI 一键登录。"""
    db = SessionLocal()
    try:
        boss = db.query(AgentModel).filter(
            AgentModel.role == AgentRole.BOSS, AgentModel.phone == DEMO_BOSS_PHONE
        ).first()
        if boss:
            return boss.id
        boss = _create_agent_row(
            db, name=DEMO_BOSS_NAME, role="boss", boss_id=None,
            service_name="", service_description="", price_per_call=0.0,
            avatar="👔", description="演示老板（人）",
            phone=DEMO_BOSS_PHONE, password=DEMO_BOSS_PASSWORD,
            starting_credits=DEFAULT_CREDITS,  # 演示登录账号也是"老板"角色，保留开户福利
        )
        print(f"[Boss] ✅ 演示老板已就绪：{DEMO_BOSS_PHONE} / {DEMO_BOSS_PASSWORD}")
        return boss.id
    finally:
        db.close()
