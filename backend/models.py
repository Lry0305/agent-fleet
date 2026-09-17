"""
数据模型 — 多认证方式
=====================
Agent 支持三种登录方式:
  - api_key:      Web Agent，用 ap_sk_xxx
  - feishu_app:   飞书机器人，用 App ID + App Secret
  - openclaw_bot: OpenClaw 机器人，用 Bot Token

AgentFleet = 支付结算层，不绑定 Agent 的具体技能。
每个 Agent 导入 SDK 即可获得支付能力。
"""

import uuid, secrets, hashlib, json
from datetime import datetime
from sqlalchemy import Column, String, Float, Text, DateTime, Boolean, Integer, Enum as SAEnum
from enum import Enum

from backend.database import Base
from backend.config import API_KEY_PREFIX


# ── 枚举 ──

class AgentRole(str, Enum):
    PROVIDER = "provider"    # worker：带 skill，接单干活
    CONSUMER = "consumer"    # 发起 Agent：agent 雇佣 agent（泛化，一人公司 v1 未用）
    BOSS = "boss"            # 老板（人）：充钱 / 派活 / 控制 / 验收，非 agent


class AuthMethod(str, Enum):
    API_KEY = "api_key"          # AgentFleet 自生成 API Key
    FEISHU_APP = "feishu_app"    # 飞书应用 App ID
    OPENCLAW_BOT = "openclaw_bot" # OpenClaw Bot Token


class TransactionStatus(str, Enum):
    PENDING = "pending"
    FUND_LOCKED = "fund_locked"
    DELIVERED = "delivered"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    REFUNDED = "refunded"
    FAILED = "failed"


class RechargeStatus(str, Enum):
    """充值订单状态：pending → paid（mock 网关回调置 paid）→ failed。"""
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


# ── Agent 模型 ──

class AgentModel(Base):
    __tablename__ = "agents"

    id = Column(String(64), primary_key=True, default=lambda: f"ag_{uuid.uuid4().hex[:12]}")
    name = Column(String(128), nullable=False)
    role = Column(SAEnum(AgentRole), nullable=False)
    boss_id = Column(String(64), nullable=True, index=True)                # 归属老板的 agent id（老板自己为 None）

    # ── 认证方式 ──
    auth_method = Column(SAEnum(AuthMethod), nullable=False, default=AuthMethod.API_KEY)
    # api_key 方式
    api_key = Column(String(64), unique=True, nullable=True, index=True)
    secret_hash = Column(String(256), nullable=True)                       # API Key 的 SHA256
    # 老板（人）登录
    phone = Column(String(32), nullable=True, index=True)                  # 老板手机号
    password_hash = Column(String(256), nullable=True)                     # 老板密码 SHA256
    # feishu_app / openclaw_bot 方式
    app_id = Column(String(128), nullable=True, index=True)                # 飞书 App ID 或 Bot 标识
    app_secret_hash = Column(String(256), nullable=True)                   # App Secret 的 SHA256

    # ── Provider 服务信息 ──
    service_name = Column(String(256), default="")
    service_description = Column(Text, default="")
    service_endpoint = Column(String(512), default="")
    price_per_call = Column(Float, default=0.0)
    is_service_active = Column(Boolean, default=True)
    workspace_mode = Column(String(32), default="isolated")   # isolated / shared_pool（老板创建时选）

    # ── 钱包 ──
    eth_address = Column(String(42), default="")
    encrypted_private_key = Column(Text, default="")

    # ── 余额 ──
    credit_balance = Column(Float, default=0.0)
    total_earned = Column(Float, default=0.0)
    total_spent = Column(Float, default=0.0)

    # ── 元数据 ──
    description = Column(Text, default="")
    avatar = Column(String(8), default="🤖")
    avatar_color = Column(String(16), default="#3370ff")   # 头像配色，跟 avatar 成套的预设组合，不单独选
    platform = Column(String(64), default="web")
    metadata_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def generate_api_key() -> str:
        return f"{API_KEY_PREFIX}{secrets.token_hex(16)}"

    @staticmethod
    def hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    @property
    def did(self) -> str:
        """DID = did:ethr:<eth_address>（身份层，地址即身份）"""
        if not self.eth_address:
            return ""
        return f"did:ethr:{self.eth_address.lower()}"

    def get_manifest(self) -> dict:
        """机器可读的能力清单（存于 metadata_json，不加列）"""
        try:
            return json.loads(self.metadata_json or "{}")
        except Exception:
            return {}

    def set_manifest(self, manifest: dict):
        self.metadata_json = json.dumps(manifest, ensure_ascii=False)


# ── 交易模型 ──

class TransactionModel(Base):
    __tablename__ = "transactions"

    id = Column(String(64), primary_key=True, default=lambda: f"tx_{uuid.uuid4().hex[:12]}")
    consumer_id = Column(String(64), nullable=False)
    provider_id = Column(String(64), nullable=False)
    service_name = Column(String(256), default="")
    amount = Column(Float, nullable=False)
    status = Column(SAEnum(TransactionStatus), default=TransactionStatus.PENDING)
    chain_request_id = Column(Integer, default=0)
    chain_tx_hash = Column(String(66), default="")
    data_hash = Column(String(66), default="")
    error_message = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── 服务模型 ──

class MessageType(str, Enum):
    """
    硅基协议语言 —— 三平面封闭动词集（标准化 + 可扩展 + 简洁）。

    类比 OS 系统调用 / 网络协议：
      控制面 = 老板喊停/调整（signal）
      数据面 = agent↔agent 干活（fork/write/exit）
      协议元语 = 可靠性（TCP ACK）

    每个动词有精确语义 + 状态转移；新增动词只改枚举 + manifest.speaks，
    老 agent 不识别则忽略 → 加动词不破坏老 agent（可扩展）。
    """
    # ── 协议元语（可靠性）──
    ACK = "ack"                       # 送达/处理确认 (TCP ACK)

    # ── 工作元语（数据面）──
    OFFER = "offer"                   # 声明能力 + 要价（能力协商）
    ACCEPT = "accept"                 # 接受 offer，契约成立 (TCP handshake)
    DECLINE = "decline"               # 拒绝 offer
    SPAWN = "spawn"                   # 派发一个 task (fork)
    STATUS = "status"                 # 进度/状态同步（心跳）
    DELIVER = "deliver"               # 交付产物（带 cid）(write(fd))
    RESULT = "result"                 # 完成（exit 0）
    ERROR = "error"                   # 失败（exit ≠ 0）

    # ── 控制元语（控制面）──
    PAUSE = "pause"                   # 暂停 (SIGSTOP)
    RESUME = "resume"                 # 恢复 (SIGCONT)
    CANCEL = "cancel"                 # 取消/回滚 (SIGTERM)
    ADJUST = "adjust"                 # 调预算/优先级

    # ── 兼容旧协议（deprecated，向后兼容，新 agent 用 spawn/offer）──
    TASK = "task"                     # → spawn
    NEGOTIATE = "negotiate"           # → offer/accept/decline


# 各角色「能听懂（接收）」的动词集 —— manifest.speaks 的默认值
PROVIDER_SPEAKS = [
    "spawn", "pause", "resume", "cancel", "adjust",
    "offer", "accept", "decline", "ack", "status",
]
BOSS_SPEAKS = [
    "result", "error", "status", "deliver", "offer", "ack",
]


# ── 消息模型（Phase 2 通信层）──

class MessageModel(Base):
    """agent ↔ agent 消息。store-and-forward：签名投递，收件方拉取。"""
    __tablename__ = "messages"

    id = Column(String(64), primary_key=True, default=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    thread_id = Column(String(64), index=True)
    sender_id = Column(String(64), index=True, nullable=False)
    sender_did = Column(String(128), default="")
    recipient_id = Column(String(64), index=True, nullable=False)
    recipient_did = Column(String(128), default="")
    protocol_version = Column(String(16), default="1.0")   # 协议版本，向后兼容 → 可扩展
    type = Column(SAEnum(MessageType), nullable=False)
    payload_json = Column(Text, default="{}")
    sig = Column(String(132), default="")
    read = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)


# ── 编排层：Job / Task（Phase 3 多任务并行）──

class JobStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskStatus(str, Enum):
    QUEUED = "queued"          # 已入队，等待依赖满足
    ASSIGNED = "assigned"      # 已派发 spawn，等 provider 认领
    RUNNING = "running"        # provider 正在执行
    PAUSED = "paused"          # 收到 pause，让出
    DELIVERED = "delivered"    # provider 已交付，等待验收（Phase 4）
    DONE = "done"              # 已验收 + 结算
    REJECTED = "rejected"      # 验收未通过，已退款（Phase 4）
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobModel(Base):
    """老板（人）的一句目标 → 一个 job。job 是多个 task 结算的聚合视图。"""
    __tablename__ = "jobs"

    id = Column(String(64), primary_key=True, default=lambda: f"job_{uuid.uuid4().hex[:12]}")
    boss_id = Column(String(64), nullable=False, index=True)   # 老板 agent（role=boss）
    goal = Column(Text, default="")
    status = Column(SAEnum(JobStatus), default=JobStatus.DRAFT)
    budget = Column(Float, default=0.0)
    priority = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TaskModel(Base):
    """一个 skill 的一次执行。depends_on 是 DAG 边：依赖全 done 才可派发。"""
    __tablename__ = "tasks"

    id = Column(String(64), primary_key=True, default=lambda: f"task_{uuid.uuid4().hex[:12]}")
    job_id = Column(String(64), nullable=False, index=True)
    provider_id = Column(String(64), nullable=False, index=True)   # 分配给哪个 provider
    skill = Column(String(128), default="")
    input_json = Column(Text, default="{}")
    output_cid = Column(String(66), default="")
    status = Column(SAEnum(TaskStatus), default=TaskStatus.QUEUED)
    depends_on = Column(Text, default="[]")   # JSON 数组：依赖的 task id 列表
    price = Column(Float, default=0.0)                # 派单时的预授权锁仓上限（不是最终结算价）
    tx_id = Column(String(64), default="")    # 关联的结算 transaction（escrow）
    require_review = Column(Boolean, default=False)   # True=老板必须手动 accept/reject（Phase 4）
    output_json = Column(Text, default="{}")          # provider 交付的 output（配合 output_cid）
    price_tier = Column(String(32), default="")       # 锁仓说明文字（沿用旧列名，内容已从"复杂度档位"改成按 token 计价的预授权说明）
    token_count = Column(Integer, default=0)          # 交付物真实 token 数（交付时才有，tiktoken 数出来的）
    settled_price = Column(Float, default=0.0)        # 按真实 token 数结算的最终价（≤ price，多退少不补）

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── 充值订单（老板充值：支付宝 / 微信 / 银行卡，mock 网关）──

class RechargeOrderModel(Base):
    """老板充值订单。生产环境把 mock 确认换成支付宝/微信异步通知，数据结构不变。"""
    __tablename__ = "recharge_orders"

    id = Column(String(64), primary_key=True, default=lambda: f"rc_{uuid.uuid4().hex[:12]}")
    boss_id = Column(String(64), nullable=False, index=True)   # 老板 agent id
    channel = Column(String(16), default="alipay")             # alipay / wechat / card
    amount_cny = Column(Float, default=0.0)                    # 充值金额（元）
    amount_credits = Column(Float, default=0.0)                # 到账 credits
    status = Column(SAEnum(RechargeStatus), default=RechargeStatus.PENDING)
    pay_url = Column(String(512), default="")                  # mock 二维码 / 收银台链接
    is_sandbox_real = Column(Boolean, default=False)           # True=真的调了支付宝沙箱网关；False=占位 mock

    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)


# ── 交付物模型（Phase 2 内容寻址）──

class ArtifactModel(Base):
    """内容寻址：cid = keccak256(content)，地址即内容哈希，不可变。"""
    __tablename__ = "artifacts"

    cid = Column(String(66), primary_key=True)          # 0x + keccak256(content)
    creator_id = Column(String(64), index=True)
    content_type = Column(String(128), default="application/octet-stream")
    filename = Column(String(256), default="")
    size = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)


class ServiceModel(Base):
    __tablename__ = "services"

    id = Column(String(64), primary_key=True, default=lambda: f"sv_{uuid.uuid4().hex[:12]}")
    provider_id = Column(String(64), nullable=False, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, default="")
    endpoint = Column(String(512), default="")
    price = Column(Float, default=0.0)
    is_active = Column(Boolean, default=True)
    chain_service_id = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── 声誉系统 ──

class ReputationModel(Base):
    __tablename__ = "reputation"

    id = Column(String(64), primary_key=True, default=lambda: f"rt_{uuid.uuid4().hex[:12]}")
    provider_id = Column(String(64), nullable=False, index=True)
    consumer_id = Column(String(64), nullable=False)
    transaction_id = Column(String(64), nullable=False, unique=True)
    score = Column(Integer, nullable=False)  # 1-5
    comment = Column(Text, default="")
    chain_tx_hash = Column(String(66), default="")

    created_at = Column(DateTime, default=datetime.utcnow)


# ── 技能包（方法论层，独立于 service_name 身份 / MCP 连接器）──
# 三层模型：service_name 是市场身份（派活/声誉靠它，见 backend/routes/jobs.py 的
# _find_provider_by_skill）；技能包是"怎么把活干好"的方法论文本，可多选、可装卸，不影响
# 身份也不影响声誉；MCP 连接器（现在只有飞书+OpenClaw 一种）决定这个 agent 技术上够不够
# 到外部世界，跟这两个都无关，是第三条独立的轴。
#
# 技能包本身只是纯文本——不是什么 AI 专属能力，任何 LLM 只要这段文字被塞进它的 prompt 就
# 能照着做。真正让它"生效"的是下面这一步：worker 轮询 GET /api/tasks 拿任务时，
# jobs.py 的 _task_to_dict() 会把这个任务的 provider 当前装配的所有技能包内容一起吐出去
# （见 tasks 字段），worker 收到后自己决定怎么塞进调用 LLM 的 prompt——这是个协议约定，
# 不需要任何框架支持，纯文本传输。

class SkillModel(Base):
    """技能包：老板自己的方法论库。一个技能包能同时装在好几个 agent 身上复用，不是
    一次性绑死某个 agent 的私有文本——boss_id 只限定"这是谁的库"，不跨老板共享。

    跟裸提示词的区别就在结构上：不是一坨文字，而是 description（说明书——干什么用的、
    什么场景该用它，类似 Claude Skill 的 SKILL.md frontmatter 里的 description，用来
    判断"这个包适不适合当前这个任务"）+ content（正文——具体怎么做，连带例子都写在这
    段里，不单独开 examples 字段，跟 SKILL.md 的 body 是一回事：怎么写例子是内容问题，
    不是模型层要拆分的字段）。两者都随任务一起吐给 worker（见 jobs.py 的 _task_to_dict）。
    """
    __tablename__ = "skills"

    id = Column(String(64), primary_key=True, default=lambda: f"skl_{uuid.uuid4().hex[:12]}")
    boss_id = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")  # 说明书：干什么用的/什么场景该用（不是正文）
    content = Column(Text, default="")   # 方法论正文（含示例、步骤），交付给 worker 的主体内容
    # 本地路径（可选，跟 content 二选一或并存）：你自己电脑上已经装好的真实技能文件夹
    # （比如 Claude Code 认得的 SKILL.md 目录）。填了这个，agentfleet-skill 的 serve() 会在
    # exec 命令后面追加 `--add-dir <path>`，让 claude/codex 这类 CLI 自己去读那个目录——
    # 不是 AgentFleet 把内容读出来拼文本，是真的把目录访问权限交给它，比 content 更接近
    # 真实的"技能"，前提是 --exec 配的是认这个参数的工具，且 worker 跟这个路径在同一台机器上。
    local_path = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentSkillModel(Base):
    """agent ↔ 技能包 的多对多装配关系——一个 agent 能同时装好几个技能包，
    一个技能包也能同时装在好几个 agent 身上。"""
    __tablename__ = "agent_skills"

    id = Column(String(64), primary_key=True, default=lambda: f"asl_{uuid.uuid4().hex[:12]}")
    agent_id = Column(String(64), nullable=False, index=True)
    skill_id = Column(String(64), nullable=False, index=True)

    installed_at = Column(DateTime, default=datetime.utcnow)


# ── OpenClaw 机器人 ──

class OpenClawBot(Base):
    __tablename__ = "openclaw_bots"

    id = Column(String(64), primary_key=True, default=lambda: f"oc_{uuid.uuid4().hex[:12]}")
    agent_id = Column(String(64), nullable=False, index=True)
    bot_token = Column(String(256), nullable=False)
    platform = Column(String(64), default="openclaw")
    webhook_url = Column(String(512), default="")
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
