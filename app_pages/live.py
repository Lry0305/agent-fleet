"""
经营台（默认页）：老板视角 —— 群概况 + 任务看板（读 + 控制）。
人（老板）定目标 / 设护栏 / 喊停调整；agent 自主干活、自动结算。
布局：左边自然语言派活对话框（主区），右边任务看板做成简略的侧边栏。
"""

import json
import re

import streamlit as st

from ui.api import api_get, api_post, api_delete
from ui.components import metric_box, job_status_badge, task_status_badge, avatar_badge
from ui.session import add_log

MSG_TYPE_LABELS = {
    "spawn": "🚀 派发任务", "offer": "🙋 报价", "accept": "🤝 接受", "decline": "🙅 婉拒",
    "status": "📡 状态", "deliver": "📦 交付", "result": "✅ 结果", "error": "❌ 错误",
    "pause": "⏸ 暂停", "resume": "▶ 恢复", "cancel": "⏹ 取消", "adjust": "🔧 调整",
    "ack": "✓ 确认",
}


def _agent_lookup():
    """agent_id → {name, avatar, avatar_color} 映射，聊天气泡用来显示是谁发的。"""
    agents = api_get("/api/boss/agents").get("agents", [])
    m = {
        a["agent_id"]: {
            "name": a.get("name", "?"),
            "avatar": a.get("avatar", "🤖"),
            "avatar_color": a.get("avatar_color"),
        }
        for a in agents
    }
    me = st.session_state.get("agent", {})
    if me.get("agent_id"):
        m[me["agent_id"]] = {
            "name": me.get("name", "老板"), "avatar": me.get("avatar", "👔"),
            "avatar_color": me.get("avatar_color", "#3370ff"),
        }
    return m


def _render_thread(jid: str, agent_map: dict, my_id: str):
    """把这个 job 的真实协议消息（SPAWN/OFFER/ACCEPT/DELIVER/...）渲染成聊天气泡——
    不是伪造的对话，是 agent 之间真实投递过的消息（backend/routes/messages.py 的存证转发）。
    """
    msgs = api_get(f"/api/messages/thread/{jid}").get("messages", [])
    if not msgs:
        st.caption("暂无协议消息")
        return
    html = ['<div class="chat-thread">']
    for m in msgs:
        sender_id = m.get("from_id")
        is_me = sender_id == my_id
        info = agent_map.get(sender_id, {})
        name = info.get("name") or ("老板" if is_me else "?")
        avatar = info.get("avatar") or ("👔" if is_me else "🤖")
        avatar_color = info.get("avatar_color")
        side = "chat-right" if is_me else ""
        mtype = m.get("type", "")
        payload = m.get("payload", {}) or {}
        payload_str = json.dumps(payload, ensure_ascii=False) if payload else ""
        ts = (m.get("created_at") or "")[11:19]
        badge = MSG_TYPE_LABELS.get(mtype, mtype)
        payload_html = (
            f'<div class="chat-payload">{payload_str}</div>'
            if payload_str and payload_str != "{}" else ""
        )
        html.append(
            f'<div class="chat-row {side}">'
            f'<div class="chat-avatar">{avatar_badge(avatar, avatar_color, size=32)}</div>'
            f'<div class="chat-bubble-wrap">'
            f'<div class="chat-meta">{name} · {ts}</div>'
            f'<div class="chat-bubble">'
            f'<span class="chat-type-badge chat-type-{mtype}">{badge}</span>'
            f'{payload_html}'
            f'</div></div></div>'
        )
    html.append('</div>')
    st.markdown("".join(html), unsafe_allow_html=True)

st.markdown('<p class="app-title">经营台</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="app-subtitle" style="color:var(--text-secondary);font-size:0.85rem">'
    "一人公司 · 老板定目标、设护栏、喊停调整；agent 自主干活、自动结算</p>",
    unsafe_allow_html=True,
)

if st.session_state.get("logged_in"):
    bal = api_get("/api/billing/balance")
    a = st.session_state.agent
    st.markdown(
        f'<div class="card card-accent" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
        f'<span>{avatar_badge(a.get("avatar","👔"), a.get("avatar_color", "#3370ff"))} '
        f'<b>{a.get("name","老板")}</b> '
        f'<span class="agent-meta">· 老板</span></span>'
        f'<span style="font-size:0.9rem">余额 <b>{bal.get("credit_balance",0):.0f} credits</b> · '
        f'¥{bal.get("credit_balance",0)*0.1:.1f}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )
elif not st.session_state.get("logged_in"):
    st.info("👔 请先在左侧边栏「一键演示登录」，登录后即可充钱、建 agent、派活。")


def _active_providers():
    """价格全平台统一，选 provider 不用再看它有没有挂牌价——只看有没有声明 service_name。"""
    agents = api_get("/api/boss/agents").get("agents", [])
    return [
        a for a in agents
        if a.get("role") == "provider" and a.get("is_service_active", True)
        and a.get("service_name")
    ]


def _match_providers(text: str, providers: list) -> dict:
    """关键词匹配：service_name（或它按下划线/横线拆开的每个词）出现在文本里就算命中。
    这不是通用语言理解——只是个能先跑起来的启发式，接了 LLM 之后可以升级成真正的语义解析。
    """
    t = text.lower()
    matched = {}
    for p in providers:
        svc = (p.get("service_name") or "").strip().lower()
        if not svc:
            continue
        words = [w for w in svc.replace("_", " ").replace("-", " ").split() if w]
        if svc in t or (words and all(w in t for w in words)):
            matched.setdefault(svc, []).append(p)
    return matched


def _extract_budget(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:credits?|cr\b|块|元)", text, re.IGNORECASE)
    return float(m.group(1)) if m else 0.0


def _dispatch_parallel(named_agents: list, named_skills: list, prompt: str) -> str:
    """多选 agent / 多选技能：在同一个 job 里一次建多个 task，互相不 depends_on——
    是真的并行（provider 各自同时接单干活），不是选好几个之后排队一个个派。
    指定 agent 的 task 直接分配给它；指定技能但没指定 agent 的 task 按 skill 匹配名下活跃
    provider（不比价，按声誉选，价格全平台统一）。
    """
    budget = _extract_budget(prompt)
    tasks, labels = [], []
    for i, a in enumerate(named_agents):
        svc = a.get("service_name")
        if not svc:
            labels.append(f"⚠️ {a.get('name')} 还没声明服务名称，跳过")
            continue
        tasks.append({
            "ref": f"a{i}", "skill": svc, "input": {"prompt": prompt},
            "provider_id": a.get("agent_id"),
        })
    for i, s in enumerate(named_skills):
        tasks.append({"ref": f"s{i}", "skill": s, "input": {"prompt": prompt}})

    if not tasks:
        return "、".join(labels) if labels else "没有可派的 task。"

    r = api_post("/api/jobs", {"goal": prompt, "budget": budget, "tasks": tasks})
    if r.get("error") or "job_id" not in r:
        return f"并行派活失败：{r.get('detail', '未知错误')}"

    for t0 in r.get("tasks", []):
        tier = f"（{t0.get('price_tier')}）" if t0.get("price_tier") else ""
        labels.append(f"**{t0.get('provider_name','?')}**（`{t0.get('skill')}` · ≤{t0.get('price', 0):.1f}cr{tier}）")
    add_log(f"🗨️ 并行派活 x{len(tasks)}: {prompt[:30]}…", "success")
    return f"已并行派给 {len(tasks)} 个：" + "、".join(labels) + "，一个 job 里同时跑，去右边任务看板里看进度。"


def _dispatch_skill(svc: str, prompt: str) -> str:
    """/服务名称 命中：跳过模糊匹配，直接按 service_name 匹配一个活跃 provider（不比价，按声誉选）。"""
    budget = _extract_budget(prompt)
    r = api_post("/api/jobs", {
        "goal": prompt, "budget": budget,
        "tasks": [{"ref": "t1", "skill": svc, "input": {"prompt": prompt}}],
    })
    if r.get("error") or "job_id" not in r:
        return f"派活失败：{r.get('detail', '未知错误')}"
    t0 = r["tasks"][0]
    tier = f"（{t0.get('price_tier')}）" if t0.get("price_tier") else ""
    add_log(f"🗨️ /{svc} 指定服务名称派活: {prompt[:30]}…", "success")
    return (
        f"已按服务名称 `{svc}` 派给 **{t0.get('provider_name','?')}**，"
        f"预授权锁仓 ≤{t0.get('price', 0):.1f} credits{tier}，按实际产出结算，去右边任务看板里看真实成交价和进度。"
    )


def _dispatch_matched(matched: dict, providers: list, prompt: str) -> str:
    """原来的关键词模糊匹配兜底——没用 @/ 精确指定时才走这条。"""
    if len(matched) == 1:
        svc = next(iter(matched))
        return _dispatch_skill(svc, prompt)
    elif len(matched) > 1:
        names = "、".join(f"`{s}`" for s in matched)
        return f"匹配到多个可能的服务名称：{names}——把想要的那个服务名称单独说一遍，比如「用 {next(iter(matched))} 做这个」，或者直接 `/{next(iter(matched))}`。"
    else:
        if providers:
            skill_list = "、".join(sorted({p.get("service_name", "") for p in providers}))
            reply = f"没匹配到已声明的服务名称。目前可派的服务名称有：{skill_list}——把服务名称带进你的话里，或者用 `/服务名称` 直接指定。"
        else:
            reply = "现在没有任何 provider 在接单——先去「我的 Agent」创建/导入一个 provider，并给它设置好服务名称。"
        return reply


def _render_dispatch_box():
    """自然语言派活对话框——不加标题/说明文字，直接就是聊天记录 + 输入框，
    尽量贴近一个真正的聊天机器人对话窗口，而不是一个带标题的"功能模块"。

    Streamlit 的 st.chat_input 就是个纯文本框，没有"敲 @ 弹出候选下拉"这种钩子，
    所以指定 agent / 指定技能用两个原生 multiselect——可以多选，多选即并行（同一个 job
    里一次建多个 task，互不 depends_on，各自独立跑）。新建/停用 agent 之后这两个下拉的
    选项也会跟着刷新。两边都不选就还是走关键词模糊匹配那套兜底逻辑。
    """
    if "dispatch_chat" not in st.session_state:
        st.session_state.dispatch_chat = []

    chat_box = st.container(height=280)
    with chat_box:
        if not st.session_state.dispatch_chat:
            st.caption("跟你的 agent 群说你要做什么，比如：帮我做一下 AAPL 的 stock_analysis")
        for msg in st.session_state.dispatch_chat[-30:]:
            with st.chat_message(msg["role"], avatar=("👔" if msg["role"] == "user" else "🤖")):
                st.markdown(msg["text"])

    providers = _active_providers()
    agent_names = [p.get("name") for p in providers if p.get("name")]
    skill_names = sorted({p.get("service_name", "") for p in providers if p.get("service_name")})

    pc1, pc2 = st.columns(2)
    with pc1:
        picked_agents = st.multiselect("🎯 指定 Agent（可多选，并行跑）", agent_names, key="dispatch_pick_agents")
    with pc2:
        picked_skills = st.multiselect(
            "🔧 指定服务名称（可多选，并行跑）", skill_names, key="dispatch_pick_skills",
            help="按服务名称路由给对应 provider——它装配了哪些技能包会自动带上，不用在这选；"
                 "去「我的技能」页管理装配。",
        )

    prompt = st.chat_input("跟你的 agent 群说你要做什么…")
    if prompt:
        st.session_state.dispatch_chat.append({"role": "user", "text": prompt})
        providers = _active_providers()

        named_agents = [p for p in providers if p.get("name") in picked_agents]

        if named_agents or picked_skills:
            reply = _dispatch_parallel(named_agents, picked_skills, prompt)
        else:
            matched = _match_providers(prompt, providers)
            reply = _dispatch_matched(matched, providers, prompt)

        st.session_state.dispatch_chat.append({"role": "assistant", "text": reply})
        st.rerun()


def _control(jid: str, action: str, data: dict = None):
    r = api_post(f"/api/jobs/{jid}/{action}", data or {})
    if r.get("error"):
        st.error(r.get("detail", f"{action} 失败"))
    else:
        st.toast(f"已 {action} job {jid}")


def _delete_job(jid: str):
    r = api_delete(f"/api/jobs/{jid}")
    if r.get("error"):
        st.error(r.get("detail", "删除失败——只有终态（done/cancelled/failed）的 job 能删，进行中的要先取消"))
    else:
        st.toast("已删除")
        st.rerun()


def _job_card(job: dict):
    """简略版 job 行：一眼看状态/进度，细节和操作都收进「详情 / 操作」里——
    给右边窄侧边栏用，不是原来那种铺满整行的大卡片。
    """
    jid = job["job_id"]
    status = job["status"]
    prog = job.get("progress", {})
    done, total = prog.get("done", 0), prog.get("total", 0)
    terminal = status in ("done", "cancelled", "failed")
    goal = job.get("goal") or "(无目标)"
    short_goal = goal if len(goal) <= 18 else goal[:18] + "…"

    with st.container(border=True):
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:center;gap:6px;font-size:0.8rem">'
            f'<b title="{goal}">{short_goal}</b>{job_status_badge(status)}</div>',
            unsafe_allow_html=True,
        )
        st.progress(
            (done / total) if total else 0.0,
            text=f"{done}/{total} · {job.get('budget', 0):.0f}cr",
        )

        with st.expander("详情 / 操作", expanded=False):
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                if st.button("⏸", key=f"pause_{jid}", help="暂停", width="stretch",
                             disabled=status not in ("draft", "running")):
                    _control(jid, "pause")
            with c2:
                if st.button("▶", key=f"resume_{jid}", help="恢复", width="stretch",
                             disabled=status != "paused"):
                    _control(jid, "resume")
            with c3:
                if st.button("⏹", key=f"cancel_{jid}", help="取消", width="stretch", disabled=terminal):
                    _control(jid, "cancel")
            with c4:
                with st.popover("🔧", disabled=terminal, help="调整预算"):
                    nb = st.number_input("新预算 (cr)", 0.0, 100000.0, float(job.get("budget", 0.0)), key=f"budget_{jid}")
                    if st.button("确认调整", key=f"adjust_{jid}", width="stretch"):
                        _control(jid, "adjust", {"budget": nb})
            with c5:
                with st.popover("🗑", disabled=not terminal,
                                 help="删除" if terminal else "终态（done/cancelled/failed）才能删"):
                    st.caption("删除后不可恢复，任务记录和协议对话会一起清掉。")
                    if st.button("确认删除", key=f"del_{jid}", width="stretch"):
                        _delete_job(jid)

            tasks = job.get("tasks", [])
            for t in tasks:
                deps = t.get("depends_on") or []
                dep_txt = " · ".join(deps) if deps else "—"
                # 按交付物真实 token 数结算：done 之前只显示预授权锁仓上限（≤，不是最终价）；
                # done 之后显示真实结算价 + 真实 token 数——这俩数字通常不一样，锁多花少是常态。
                if t.get("status") == "done" and t.get("settled_price", 0) > 0:
                    price_txt = f'{t.get("settled_price", 0):.1f}cr（{t.get("token_count", 0)} tok）'
                else:
                    price_txt = f'≤{t.get("price", 0):.1f}cr 预授权'
                st.markdown(
                    f'<div class="log-entry" style="font-size:0.75rem;flex-wrap:wrap">'
                    f'{task_status_badge(t.get("status"))} '
                    f'<b>{t.get("provider_name", "?")}</b> · '
                    f'{t.get("skill", "—")} · {price_txt} '
                    f'<span style="color:#94a3b8;font-size:0.68rem">依赖: {dep_txt}</span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                out = t.get("output") or {}
                if out:
                    st.json(out)

            st.markdown('<div class="chat-meta" style="margin-top:6px">🗨️ 协议对话</div>', unsafe_allow_html=True)
            _render_thread(jid, st.session_state.get("_agent_lookup_cache", {}), st.session_state.get("agent", {}).get("agent_id", ""))


# job 分类筛选：全部 / 进行中 / 已完成 / 已终止（取消或失败）——job 攒多了方便按状态过一遍，
# 而不是所有历史 job 无限往下堆。
STATUS_GROUPS = {
    "全部": None,
    "进行中": ("draft", "running", "paused"),
    "已完成": ("done",),
    "已终止": ("cancelled", "failed"),
}


@st.fragment(run_every="3s")
def render_board():
    stats = api_get("/api/billing/stats")
    jobs = api_get("/api/jobs").get("jobs", [])

    running = sum(1 for j in jobs if j["status"] in ("running", "paused"))
    st.markdown("**任务看板**")
    st.caption(
        f"👥{stats.get('total_agents', 0)} · 🏃{running}运行中 · "
        f"✅{stats.get('confirmed_transactions', 0)}已结算 · "
        f"💰{stats.get('total_volume_credits', 0):.0f}cr"
    )

    if not jobs:
        st.caption("还没有 job —— 左边派活，或跑 `run_demo.py` 后自动出现。")
        return

    filt = st.selectbox(
        "筛选", list(STATUS_GROUPS.keys()),
        key="board_status_filter", label_visibility="collapsed",
    )
    allowed = STATUS_GROUPS[filt]
    shown = [j for j in jobs if allowed is None or j["status"] in allowed]
    if not shown:
        st.caption(f"「{filt}」下没有 job")
        return

    st.session_state["_agent_lookup_cache"] = _agent_lookup()
    for job in shown:
        _job_card(job)


if st.session_state.get("logged_in"):
    col_chat, col_board = st.columns([2, 1])
    with col_chat:
        _render_dispatch_box()
    with col_board:
        render_board()
else:
    render_board()
