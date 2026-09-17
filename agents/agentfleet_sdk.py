"""
AgentFleet SDK v3.0 — Agent 通用支付层
=====================================
AgentFleet 是纯支付/结算层。每个 Agent 自带技能，导入 SDK 获得支付能力。

三种认证方式:
  - api_key:      Web Agent，用 ap_sk_xxx
  - feishu_app:   飞书机器人，用 App ID + App Secret
  - openclaw_bot: OpenClaw 机器人，用 Bot Token

快速开始:
    from agents.agentfleet_sdk import AgentFleetClient
    client = AgentFleetClient()

    # ── 方式 1: API Key ──
    result = client.register("StockBot", "provider", service_name="stock_query", price=5)
    client.login(api_key=result["api_key"])

    # ── 方式 2: 飞书 App ──
    result = client.register("FeishuBot", "provider",
        auth_method="feishu_app", app_id="cli_abc", app_secret="secret123")
    client.login_with_feishu(app_id="cli_abc", app_secret="secret123")

    # ── 方式 3: OpenClaw Bot ──
    result = client.register("OpenClawBot", "consumer",
        auth_method="openclaw_bot", app_id="bot_token_xxx")
    client.login_with_openclaw(bot_token="bot_token_xxx")

    # ── 通用操作 ──
    tx = client.pay(provider_id="ag_xxx", amount=10)
    client.confirm(tx.transaction_id)
    client.execute_task("data_query", "查AAPL", consumer_id="ag_xxx", charge_amount=5)
"""

import requests
import uuid
import base64
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from eth_account import Account
from eth_account.messages import encode_defunct


@dataclass
class AgentInfo:
    agent_id: str = ""
    name: str = ""
    role: str = ""
    credit_balance: float = 0.0
    total_earned: float = 0.0
    total_spent: float = 0.0
    avatar: str = "🤖"


@dataclass
class TransactionResult:
    transaction_id: str = ""
    amount: float = 0.0
    status: str = ""
    provider_name: str = ""
    consumer_name: str = ""
    chain_tx_hash: str = ""
    message: str = ""


class AgentFleetClient:
    """
    AgentFleet 通用客户端。
    支持 Agent 注册、登录、支付、执行、查询。
    """

    def __init__(self, api_base: str = "http://127.0.0.1:8765", workspace_root: str = "agent_workspaces"):
        self.api_base = api_base.rstrip("/")
        self.api_key: str = ""
        self.private_key: str = ""   # 以太坊私钥，用于消息签名（register 时一次性返回）
        self.did: str = ""           # did:ethr:0x...
        self.eth_address: str = ""
        self._agent: Optional[AgentInfo] = None
        self.workspace_root: str = workspace_root  # agent home 目录根（workspace_root/<agent_id>/）

    # ── 签名（与 backend/identity.py 保持同一规范）──

    def _sign(self, envelope: dict) -> str:
        """对消息信封做 EIP-191 签名。canonical = json(sort_keys, 紧凑分隔)。"""
        env = {k: v for k, v in envelope.items() if k != "sig"}
        canonical = json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        msg = encode_defunct(canonical)
        return Account.sign_message(msg, private_key=self.private_key).signature.hex()

    # ── 内部 ────────────────────────────────────────────

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _get(self, path: str) -> dict:
        r = requests.get(f"{self.api_base}{path}", headers=self._headers(), timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}

    def _post(self, path: str, data: dict = None) -> dict:
        r = requests.post(f"{self.api_base}{path}", headers=self._headers(), json=data or {}, timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}

    def _put(self, path: str, data: dict = None) -> dict:
        r = requests.put(f"{self.api_base}{path}", headers=self._headers(), json=data or {}, timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}

    # ── 公开方法 ─────────────────────────────────────────

    @property
    def agent(self) -> Optional[AgentInfo]:
        return self._agent

    def register(
        self,
        name: str,
        role: str,
        *,
        auth_method: str = "api_key",
        app_id: str = "",
        app_secret: str = "",
        service_name: str = "",
        service_description: str = "",
        price_per_call: float = 0.0,
        description: str = "",
        avatar: str = "🤖",
        platform: str = "web",
    ) -> dict:
        """
        注册新 Agent。支持三种认证方式:
          - api_key:      自动生成 ap_sk_xxx
          - feishu_app:   需提供 app_id + app_secret (飞书 App ID + App Secret)
          - openclaw_bot: 需提供 app_id (Bot Token)

        返回包含 api_key 的字典。无论哪种认证方式都会生成 API Key 备用。
        ⚠️ 请妥善保存 api_key / app_secret。
        """
        payload = {
            "name": name, "role": role, "auth_method": auth_method,
            "avatar": avatar, "description": description, "platform": platform,
        }
        if auth_method in ("feishu_app", "openclaw_bot"):
            payload["app_id"] = app_id
            if app_secret:
                payload["app_secret"] = app_secret
        if role == "provider":
            payload.update({
                "service_name": service_name,
                "service_description": service_description,
                "price_per_call": price_per_call,
            })
        r = self._post("/api/agents/register", payload)
        if not r.get("error"):
            self.private_key = r.get("private_key", "")
            self.did = r.get("did", "")
            self.eth_address = r.get("eth_address", "")
        return r

    def _login_with_result(self, result: dict) -> AgentInfo:
        """内部: 解析登录结果并设置认证令牌"""
        api_key = result.get("api_key", "")
        if api_key:
            self.api_key = api_key
        self.did = result.get("did", "")
        self.eth_address = result.get("eth_address", "")
        self._agent = AgentInfo(
            agent_id=result.get("agent_id", ""),
            name=result.get("name", ""),
            role=result.get("role", ""),
            credit_balance=result.get("credit_balance", 0),
            total_earned=result.get("total_earned", 0),
            total_spent=result.get("total_spent", 0),
            avatar=result.get("avatar", "🤖"),
        )
        self.ensure_workspace()  # 登录即建 home 目录（inbox/artifacts/state/logs）
        return self._agent

    def login(self, api_key: str = "") -> AgentInfo:
        """
        通过 API Key 登录 (ap_sk_xxx)。
        登录后才能调用 pay/confirm/execute 等需要身份的方法。

        也可以不传 api_key 直接设置 self.api_key 后调用。
        """
        if api_key:
            self.api_key = api_key
        result = self._post("/api/agents/login", {
            "auth_method": "api_key",
            "api_key": self.api_key,
        })
        if result.get("error"):
            raise ValueError(f"登录失败: {result.get('detail')}")
        return self._login_with_result(result)

    def login_with_feishu(self, app_id: str, app_secret: str) -> AgentInfo:
        """
        通过飞书 App ID + App Secret 登录。
        适用于飞书机器人 Agent 身份认证。

        Args:
            app_id: 飞书应用的 App ID (如 cli_xxx)
            app_secret: 飞书应用的 App Secret
        Returns:
            AgentInfo: 登录后的 Agent 信息
        """
        result = self._post("/api/agents/login", {
            "auth_method": "feishu_app",
            "app_id": app_id,
            "app_secret": app_secret,
        })
        if result.get("error"):
            raise ValueError(f"飞书登录失败: {result.get('detail')}")
        return self._login_with_result(result)

    def login_with_openclaw(self, bot_token: str) -> AgentInfo:
        """
        通过 OpenClaw Bot Token 登录。
        适用于 OpenClaw 机器人身份认证。

        Args:
            bot_token: OpenClaw Bot 的 Token
        Returns:
            AgentInfo: 登录后的 Agent 信息
        """
        result = self._post("/api/agents/login", {
            "auth_method": "openclaw_bot",
            "bot_token": bot_token,
        })
        if result.get("error"):
            raise ValueError(f"OpenClaw 登录失败: {result.get('detail')}")
        return self._login_with_result(result)

    def get_balance(self) -> dict:
        """查询当前 Agent 的 credit 余额和 ETH 等值"""
        return self._get("/api/billing/balance")

    def list_agents(self, role: str = "") -> list:
        """浏览平台上所有公开 Agent"""
        params = {}
        if role:
            params["role"] = role
        result = self._get("/api/agents/all")
        return result.get("agents", [])

    def pay(self, provider_id: str, amount: float, service_id: str = "") -> TransactionResult:
        """
        【Consumer】发起支付：锁定 credits → 创建链上 Escrow。
        资金锁定在智能合约中，Provider 暂时无法取走。
        """
        data = {"provider_id": provider_id, "amount": amount}
        if service_id:
            data["service_id"] = service_id
        r = self._post("/api/billing/pay", data)
        if r.get("error"):
            raise RuntimeError(f"支付失败: {r.get('detail')}")
        return TransactionResult(
            transaction_id=r.get("transaction_id", ""),
            amount=amount,
            status=r.get("status", "fund_locked"),
            provider_name=r.get("provider_name", ""),
            chain_tx_hash=r.get("chain_tx_hash", ""),
            message=r.get("message", ""),
        )

    def confirm(self, tx_id: str) -> dict:
        """
        【Consumer】确认交付 → 链上合约释放 ETH 给 Provider。
        不可逆操作，确认后资金立即转入 Provider。
        """
        return self._post(f"/api/billing/confirm/{tx_id}")

    def execute_task(
        self,
        task_type: str,
        target: str,
        *,
        consumer_id: str,
        charge_amount: float,
        service_id: str = "",
        parameters: dict = None,
    ) -> dict:
        """
        【Provider】执行任务并自动计费。
        执行完成后自动从 Consumer 扣款转入 Provider。
        """
        payload = {
            "task_type": task_type,
            "target": target,
            "consumer_id": consumer_id,
            "charge_amount": charge_amount,
            "service_id": service_id,
            "parameters": parameters or {},
        }
        return self._post("/api/openclaw/execute", payload)

    def get_transactions(self, limit: int = 20) -> list:
        """查询当前 Agent 的交易记录"""
        r = self._get(f"/api/billing/transactions?limit={limit}")
        return r.get("transactions", [])

    def get_stats(self) -> dict:
        """查询平台统计数据"""
        return self._get("/api/billing/stats")

    def register_service(self, name: str, description: str = "", price: float = 0.0) -> dict:
        """【Provider】注册服务到市场——注意：这个"服务市场"（ServiceModel）跟
        job/task 竞价实际读的 agent.service_name / price_per_call 是两套不相关的数据，
        写这里不会让你在经营台派活时被匹配到。要真的挂牌接单，用 update_profile()。"""
        return self._post("/api/services/register", {
            "name": name, "description": description, "price": price,
        })

    def update_profile(
        self,
        service_name: str = None,
        service_description: str = None,
        price_per_call: float = None,
        is_service_active: bool = None,
    ) -> dict:
        """【Provider】更新自己的挂牌信息——PUT /api/agents/me，写的就是 job/task 竞价
        实际读取的 agent.service_name / price_per_call，这里改了才会真的影响能不能被派到活。
        只传你要改的字段，其余留 None 就不动。"""
        payload = {
            k: v for k, v in {
                "service_name": service_name,
                "service_description": service_description,
                "price_per_call": price_per_call,
                "is_service_active": is_service_active,
            }.items() if v is not None
        }
        return self._put("/api/agents/me", payload)

    def bind_robot(self, bot_token: str, platform: str = "openclaw", webhook_url: str = "") -> dict:
        """【Provider】绑定 OpenClaw/飞书 机器人"""
        return self._post("/api/openclaw/register-bot", {
            "bot_token": bot_token, "platform": platform, "webhook_url": webhook_url,
        })

    # ── Phase 2：身份 / 发现 / 通信 / 交付 ──────────────────────────

    def identify(self) -> dict:
        """identify 原语：返回自己的 DID + 能力清单（/me）。"""
        return self._get("/api/agents/me")

    def discover(self, role: str = "") -> list:
        """discover 原语：列出群里的 Agent（可带 role 过滤）。"""
        result = self._get("/api/agents/all")
        return result.get("agents", [])

    def get_manifest(self, agent_id: str) -> dict:
        """获取某个 Agent 的机器可读能力清单。"""
        return self._get(f"/api/agents/{agent_id}/manifest")

    def send_message(self, to: str, type_: str, payload: dict = None, thread_id: str = "") -> dict:
        """message 原语：签名后投递给收件方（to 为 DID）。"""
        if not self.private_key or not self.did:
            raise RuntimeError("尚未持有私钥/身份（先 register）")
        envelope = {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "from": self.did,
            "to": to,
            "thread_id": thread_id or f"thr_{uuid.uuid4().hex[:12]}",
            "type": type_,
            "payload": payload or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        sig = self._sign(envelope)
        return self._post("/api/messages/send", {"message": envelope, "sig": sig})

    def receive(self, unread_only: bool = False, limit: int = 50) -> dict:
        """拉取收件箱（store-and-forward：收件方主动拉）。"""
        return self._get(f"/api/messages/inbox?unread_only={unread_only}&limit={limit}")

    def mark_read(self, message_id: str) -> dict:
        """标记某条消息已读（消费信号 / spawn 后防重放）。"""
        return self._post(f"/api/messages/{message_id}/read")

    # ── 工作空间（home 目录 + checkpoint 状态存续）──────────────

    @property
    def workspace_dir(self) -> str:
        """agent 的 home 目录：workspace_root/<agent_id>/。"""
        aid = self._agent.agent_id if self._agent else "pending"
        return str(Path(self.workspace_root) / aid)

    def ensure_workspace(self) -> str:
        """确保 home 目录布局存在（inbox / artifacts / state / logs）+ manifest 快照。幂等。"""
        ws = Path(self.workspace_dir)
        for sub in ("inbox", "artifacts", "state", "logs"):
            (ws / sub).mkdir(parents=True, exist_ok=True)
        manifest_path = ws / "manifest.json"
        if not manifest_path.exists():
            aid = self._agent.agent_id if self._agent else ""
            manifest = {}
            if aid:
                try:
                    manifest = self.get_manifest(aid)
                except Exception:
                    manifest = {}
            manifest.setdefault("agent_id", aid)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        return str(ws)

    def save_checkpoint(self, task_id: str, state: dict) -> str:
        """把任务状态落盘到 home/state/<task_id>.json（可存续，重启可续跑）。返回文件路径。"""
        data = dict(state)
        data.setdefault("task_id", task_id)
        data["updated_at"] = datetime.utcnow().isoformat()
        self.ensure_workspace()
        p = Path(self.workspace_dir) / "state" / f"{task_id}.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return str(p)

    def load_checkpoint(self, task_id: str) -> dict:
        """读回某个 task 的 checkpoint 状态（不存在则返回空 dict）。"""
        p = Path(self.workspace_dir) / "state" / f"{task_id}.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def deliver(self, tx_id: str, content, content_type: str = "application/json", filename: str = "") -> dict:
        """deliver 原语：存交付物（内容寻址）→ 链上存证 → status=DELIVERED。"""
        if isinstance(content, str):
            content = content.encode("utf-8")
        data_b64 = base64.b64encode(content).decode()
        r = self._post(f"/api/billing/deliver/{tx_id}", {
            "data_b64": data_b64, "content_type": content_type, "filename": filename,
        })
        if r.get("error"):
            raise RuntimeError(f"交付失败: {r.get('detail')}")
        return r

    def get_events(self, limit: int = 50) -> dict:
        """emit 原语（读）：统一事件流，人可视化用。"""
        return self._get(f"/api/events/stream?limit={limit}")


# ── Phase 3：编排 / 控制（provider 侧协作式 checkpoint + boss 侧薄封装）────────

    def _control_verbs(self, task_id: str) -> list:
        """拉取收件箱里针对该 task 的未读控制信号（pause/resume/cancel/adjust）。"""
        inbox = self.receive(unread_only=True, limit=50)
        verbs = ("pause", "resume", "cancel", "adjust")
        return [
            m for m in inbox.get("messages", [])
            if m.get("type") in verbs and (m.get("payload") or {}).get("task_id") == task_id
        ]

    def send_status(self, task_id: str, status: str, boss_did: str, job_id: str) -> dict:
        """status 原语：向老板汇报进度（走签名消息邮箱）。"""
        return self.send_message(boss_did, "status", {"task_id": task_id, "status": status}, thread_id=job_id)

    def checkpoint(self, task_id: str, boss_did: str, job_id: str, state: dict, poll: float = 0.3, log=None) -> str:
        """协作式 checkpoint（skill 生成器每步之间调用）。

        类比 asyncio 协作式调度 / Celery abortable task：
        ① 让出控制 ② 拉取 pause/cancel/adjust ③ 按信号响应
        （暂停 → emit status:paused 并阻塞等 resume/cancel；取消 → 返回 cancelled；调整 → 改 budget + ack）。

        state: 可变字典，记录任务运行状态（status/budget）。返回 "running" / "cancelled"。
        log: 可选回调（str -> None），用于上层打印控制面事件（demo 用）。
        """
        def _log(s):
            if log:
                log(s)

        while True:
            changed = False
            for m in self._control_verbs(task_id):
                t = m["type"]
                if t == "pause" and state.get("status") != "paused":
                    state["status"] = "paused"
                    self.send_status(task_id, "paused", boss_did, job_id)
                    _log("⏸ 收到 pause，已暂停（checkpoint 让出）")
                    changed = True
                elif t == "resume" and state.get("status") == "paused":
                    state["status"] = "running"
                    self.send_status(task_id, "running", boss_did, job_id)
                    _log("▶ 收到 resume，继续执行")
                    changed = True
                elif t == "cancel":
                    state["status"] = "cancelled"
                    self.mark_read(m["id"])
                    self.save_checkpoint(task_id, state)
                    _log("⛔ 收到 cancel，让出")
                    return "cancelled"
                elif t == "adjust":
                    state["budget"] = m.get("payload", {}).get("budget", state.get("budget", 0))
                    self.send_message(boss_did, "ack", {"task_id": task_id, "action": "adjust"}, thread_id=job_id)
                    _log(f"🔧 收到 adjust，预算改为 {state.get('budget')}")
                    changed = True
                self.mark_read(m["id"])  # 消费掉这条信号，避免重放
            if changed:
                self.save_checkpoint(task_id, state)  # 控制面状态存续（可重启续跑）
            if state.get("status") == "paused":
                time.sleep(poll)  # 阻塞，直到 resume/cancel
                continue
            return state.get("status", "running")

    def mark_task_done(self, task_id: str, cid: str = "", output: dict = None) -> dict:
        """provider 完成任务 → 自动结算 + 推进 DAG（交付即放款）。"""
        return self._post(f"/api/tasks/{task_id}/done", {"cid": cid, "output": output or {}})

    def mark_task_fail(self, task_id: str) -> dict:
        """provider 标记任务失败。"""
        return self._post(f"/api/tasks/{task_id}/fail", {})

    def list_my_tasks(self, status: str = "") -> list:
        """provider 拉自己被派到的任务。agentfleet-skill 的 serve 常驻进程用这个轮询，
        取代老的「盯 transactions 变化」——直接问「有没有分给我的 task」，语义更准。
        """
        path = "/api/tasks"
        if status:
            path += f"?status={status}"
        return self._get(path).get("tasks", [])

    # ── boss 侧（人通过经营台 / demo 脚本调用）──────────────

    def deposit(self, amount_credits: float) -> dict:
        """【老板】演示充值（Hardhat funder 代付）。"""
        return self._post("/api/billing/deposit", {"amount_credits": amount_credits})

    # ── 老板账号（人登录 / 创建名下 agent）──────────────

    def register_boss(self, name: str, phone: str, password: str) -> dict:
        """【老板】注册老板账号（手机号 + 密码）。"""
        return self._post("/api/boss/register", {"name": name, "phone": phone, "password": password})

    def login_boss(self, phone: str, password: str) -> dict:
        """【老板】手机号 + 密码登录，返回 api_key（写入 self.api_key）。"""
        r = self._post("/api/boss/login", {"phone": phone, "password": password})
        if r.get("api_key"):
            self.api_key = r["api_key"]
        return r

    def create_agent(self, name: str, role: str = "provider", *, service_name: str = "",
                     service_description: str = "", price_per_call: float = 0.0,
                     avatar: str = "🤖", description: str = "") -> dict:
        """【老板】创建名下 agent（返回 {agent, api_key}，api_key 给 worker 运行时用）。"""
        return self._post("/api/boss/agents", {
            "name": name, "role": role, "service_name": service_name,
            "service_description": service_description, "price_per_call": price_per_call,
            "avatar": avatar, "description": description,
        })

    def list_agents(self) -> list:
        """【老板】列出名下 agent。"""
        return self._get("/api/boss/agents").get("agents", [])

    def recharge(self, channel: str, amount_cny: float) -> dict:
        """【老板】建充值订单（支付宝/微信/银行卡，mock 网关）。"""
        return self._post("/api/boss/recharge", {"channel": channel, "amount_cny": amount_cny})

    def confirm_recharge(self, order_id: str) -> dict:
        """【老板】模拟支付成功（生产换成支付宝/微信异步通知）。"""
        return self._post(f"/api/boss/recharge/{order_id}/confirm")

    def spawn_job(self, goal: str, tasks: list, budget: float = 0.0, priority: int = 0) -> dict:
        """【老板】派活：一句目标 + 任务 DAG。
        tasks = [{ref, provider_id, skill, input, price, depends_on: [ref,...]}]。"""
        return self._post("/api/jobs", {
            "goal": goal, "budget": budget, "priority": priority, "tasks": tasks,
        })

    def list_jobs(self) -> list:
        return self._get("/api/jobs").get("jobs", [])

    def get_job(self, job_id: str) -> dict:
        return self._get(f"/api/jobs/{job_id}")

    def pause_job(self, job_id: str) -> dict:
        return self._post(f"/api/jobs/{job_id}/pause")

    def resume_job(self, job_id: str) -> dict:
        return self._post(f"/api/jobs/{job_id}/resume")

    def cancel_job(self, job_id: str) -> dict:
        return self._post(f"/api/jobs/{job_id}/cancel")

    def adjust_job(self, job_id: str, budget: float) -> dict:
        return self._post(f"/api/jobs/{job_id}/adjust", {"budget": budget})
