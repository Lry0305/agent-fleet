"""
AgentPay — Streamlit 前端 v3.0
===============================
登录 = 你的身份 (我是谁) — 支持 API Key / 飞书 App ID / OpenClaw Bot
选择 = 交易对手 (跟谁交易, 快捷方式)
交易 = 实际支付/执行
"""

import streamlit as st
import requests, os
from datetime import datetime

API_BASE = os.getenv("AGENTPAY_API", "http://127.0.0.1:8765")

st.set_page_config(page_title="AgentPay", page_icon=None, layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    /* === 统一配色 (淡紫主题) === */
    :root {
        --primary: #6d28d9;
        --primary-light: #8b5cf6;
        --primary-bg: #f5f3ff;
        --primary-border: #ede9fe;
        --bg: #faf9ff;
        --card: #ffffff;
        --border: #e5e0f0;
        --text: #1e1b2e;
        --text-secondary: #6d657a;
        --success: #7c3aed;
        --warning: #9333ea;
    }
    * { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
    .stApp { background: var(--bg); }
    .block-container { max-width: 1280px; padding: 1.5rem 2rem; }
    .app-title { font-size: 1.5rem; font-weight: 700; color: var(--primary); margin: 0; }
    .section-title { font-size: 1rem; font-weight: 600; color: var(--text); margin: 8px 0; }
    .card { background: var(--card); border-radius: 10px; padding: 16px;
            box-shadow: 0 1px 2px rgba(109,40,217,0.04); border: 1px solid var(--border); margin-bottom: 10px; }
    .card-accent { border-left: 3px solid var(--primary); }
    .tag { font-size: 0.68rem; font-weight: 600; padding: 2px 8px; border-radius: 4px; display: inline-block; }
    .tag-provider { background: var(--primary-bg); color: var(--primary); }
    .tag-consumer { background: #f1edf7; color: #5b4d72; }
    .agent-meta { font-size: 0.8rem; color: var(--text-secondary); }
    .metric-box { background: var(--card); border: 1px solid var(--border);
                  border-radius: 10px; padding: 14px; text-align: center; }
    .metric-val { font-size: 1.3rem; font-weight: 700; color: var(--primary); }
    .metric-lbl { font-size: 0.72rem; color: var(--text-secondary); }
    header[data-testid="stHeader"] { background: transparent; }
    #MainMenu, footer { visibility: hidden; }
    .flow-node { padding: 12px 16px; border-radius: 10px; text-align: center; font-weight: 600;
                 background: var(--card); border: 1px solid var(--border); color: var(--text); }
    .flow-consumer { background: var(--primary-bg); border-color: var(--primary-light); color: var(--primary); }
    .flow-contract { background: #f8f6ff; border: 2px dashed var(--primary-light); color: var(--text-secondary); }
    .flow-provider { background: var(--primary-bg); border-color: var(--primary-light); color: var(--primary); }
    .chain-badge { background: var(--primary-bg); color: var(--primary); font-size: 0.7rem;
                   padding: 2px 6px; border-radius: 4px; font-family: monospace;
                   border: 1px solid var(--primary-border); }
    .no-chain-badge { background: #f8f6ff; color: var(--text-secondary); font-size: 0.7rem;
                      padding: 2px 6px; border-radius: 4px; border: 1px solid var(--border); }
    .stButton>button { border-radius: 6px; font-weight: 500; }
    /* 强制所有 primary 按钮为紫色 (覆盖 Streamlit 默认红/蓝) */
    .stButton>button[kind="primary"],
    .stButton>button[data-testid="baseButton-primary"],
    .stButton>button[kind="primaryFormSubmit"],
    button[data-testid="baseButton-primaryFormSubmit"],
    .stFormSubmitButton>button {
        background-color: var(--primary) !important;
        border-color: var(--primary) !important;
        color: white !important;
    }
    .stButton>button[kind="primary"]:hover,
    .stButton>button[data-testid="baseButton-primary"]:hover,
    .stButton>button[kind="primaryFormSubmit"]:hover,
    button[data-testid="baseButton-primaryFormSubmit"]:hover,
    .stFormSubmitButton>button:hover {
        background-color: #5b21b6 !important;
        border-color: #5b21b6 !important;
    }
    .stButton>button[kind="secondary"],
    .stButton>button[data-testid="baseButton-secondary"] {
        color: var(--primary) !important;
        border-color: var(--primary-border) !important;
        background-color: white !important;
    }
    .stButton>button[kind="secondary"]:hover,
    .stButton>button[data-testid="baseButton-secondary"]:hover {
        background-color: var(--primary-bg) !important;
        border-color: var(--primary-light) !important;
    }
    /* Streamlit 成功/错误框统一紫色 */
    div[data-testid="stAlert"] { border-color: var(--primary-border) !important; }
    /* 日志卡片 */
    .log-entry { padding: 6px 12px; margin: 2px 0; border-radius: 6px; font-size: 0.8rem;
                 display: flex; align-items: center; gap: 8px; }
    .log-entry:hover { background: var(--primary-bg); }
    .log-time { color: var(--text-secondary); min-width: 60px; font-size: 0.75rem; }
    .log-msg { color: var(--text); }
</style>
""", unsafe_allow_html=True)


def init():
    for k, v in {
        "logged_in": False, "agent": {}, "api_key": "", "logs": [],
        "counterparty": {}, "last_tx_id": "", "last_tx_amount": 0.0,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v
init()


def add_log(msg, level="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.append({"time": ts, "msg": msg, "level": level})
    st.session_state.logs = st.session_state.logs[-200:]


def api_h():
    h = {"Content-Type": "application/json"}
    if st.session_state.api_key:
        h["Authorization"] = f"Bearer {st.session_state.api_key}"
    return h


def api_get(path, params=None):
    try:
        r = requests.get(f"{API_BASE}{path}", headers=api_h(), params=params, timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}
    except Exception as e:
        return {"error": True, "detail": str(e)}


def api_post(path, data=None):
    try:
        r = requests.post(f"{API_BASE}{path}", headers=api_h(), json=data or {}, timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}
    except Exception as e:
        return {"error": True, "detail": str(e)}


def refresh_me():
    me = api_get("/api/agents/me")
    if not me.get("error"):
        st.session_state.agent = me


# ═══════ 侧边栏: 身份登录 ═══════
def sidebar():
    with st.sidebar:
        st.markdown('<p class="app-title" style="font-size:1.3rem">AgentPay</p>', unsafe_allow_html=True)
        st.caption("AI Agent 去中心化支付平台")

        if not st.session_state.logged_in:
            st.divider()
            st.markdown("**登录**")
            mode = st.radio("", ["登录", "注册"], horizontal=True, label_visibility="collapsed")

            if "注册" in mode:
                auth_method = st.selectbox("认证方式", ["api_key", "feishu_app", "openclaw_bot"],
                    format_func=lambda x: {"api_key":"API Key","feishu_app":"飞书 App ID","openclaw_bot":"OpenClaw Bot"}[x])

                with st.form("reg"):
                    name = st.text_input("Agent 名称", placeholder="例: StockAnalyst")
                    role = st.selectbox("角色", ["provider","consumer"],
                        format_func=lambda x: "🟦 Provider (服务方)" if x=="provider" else "🩷 Consumer (消费方)")

                    # 飞书/OpenClaw 需要额外字段
                    app_id_val = ""
                    app_secret_val = ""
                    if auth_method == "feishu_app":
                        app_id_val = st.text_input("飞书 App ID", placeholder="cli_xxx")
                        app_secret_val = st.text_input("飞书 App Secret", type="password", placeholder="your_app_secret")
                    elif auth_method == "openclaw_bot":
                        app_id_val = st.text_input("Bot Token", placeholder="bot_token_xxx")

                    if role == "provider":
                        svc = st.text_input("服务名称", placeholder="例: stock_analysis")
                        svc_desc = st.text_input("服务描述", placeholder="例: 股票技术分析")
                        price = st.number_input("价格 (credits)", 0.0, 1000.0, 5.0)
                    else:
                        svc, svc_desc, price = "", "", 0
                    avatar = st.text_input("头像", "🤖")
                    desc = st.text_input("简介", placeholder="一句话介绍你的 Agent")

                    if st.form_submit_button("注册", type="primary", use_container_width=True):
                        payload = {
                            "name": name, "role": role, "auth_method": auth_method,
                            "avatar": avatar, "description": desc,
                        }
                        if auth_method in ("feishu_app", "openclaw_bot"):
                            payload["app_id"] = app_id_val
                            if app_secret_val:
                                payload["app_secret"] = app_secret_val
                        if role == "provider":
                            payload.update({"service_name": svc, "service_description": svc_desc, "price_per_call": price})
                        r = api_post("/api/agents/register", payload)
                        if r.get("error"):
                            st.error(r.get("detail"))
                        else:
                            # 用返回的 api_key 自动登录
                            api_key = r.get("api_key", "")
                            st.session_state.api_key = api_key
                            st.session_state.agent = r; st.session_state.logged_in = True
                            auth_label = {"api_key":"API Key","feishu_app":"飞书 App","openclaw_bot":"OpenClaw Bot"}[auth_method]
                            add_log(f"✅ [{r['name']}] 注册成功 ({auth_label})，已自动登录", "success")
                            if api_key:
                                add_log(f"🔑 备用 API Key: {api_key}", "info")
                            st.rerun()
            else:
                login_method = st.selectbox("认证方式", ["api_key", "feishu_app", "openclaw_bot"],
                    format_func=lambda x: {"api_key":"API Key","feishu_app":"飞书 App ID","openclaw_bot":"OpenClaw Bot"}[x])
                with st.form("login"):
                    login_payload = {"auth_method": login_method}
                    if login_method == "api_key":
                        key = st.text_input("API Key", type="password", placeholder="ap_sk_...")
                        login_payload["api_key"] = key
                    elif login_method == "feishu_app":
                        feishu_id = st.text_input("飞书 App ID", placeholder="cli_xxx")
                        feishu_secret = st.text_input("飞书 App Secret", type="password", placeholder="your_app_secret")
                        login_payload["app_id"] = feishu_id
                        login_payload["app_secret"] = feishu_secret
                    elif login_method == "openclaw_bot":
                        bot_tok = st.text_input("Bot Token", type="password", placeholder="bot_token_xxx")
                        login_payload["bot_token"] = bot_tok

                    if st.form_submit_button("登录", type="primary", use_container_width=True):
                        r = api_post("/api/agents/login", login_payload)
                        if r.get("error"):
                            st.error(f"登录失败: {r.get('detail','认证无效')}")
                        else:
                            # 获取 api_key 用于后续请求
                            api_key = r.get("api_key", "")
                            st.session_state.api_key = api_key if api_key else key
                            st.session_state.agent = r; st.session_state.logged_in = True
                            auth_label = {"api_key":"API Key","feishu_app":"飞书","openclaw_bot":"OpenClaw"}[login_method]
                            add_log(f"👋 欢迎回来，{r['name']}！(通过 {auth_label} 登录)", "success")
                            st.rerun()
        else:
            a = st.session_state.agent
            st.success("已登录")
            st.markdown(f"**{a.get('avatar','')} {a.get('name','')}**")
            role_cls = "tag-provider" if a.get("role")=="provider" else "tag-consumer"
            st.markdown(f'<span class="tag {role_cls}">{a.get("role")}</span>', unsafe_allow_html=True)

            # 显示认证方式
            auth_m = a.get("auth_method", "api_key")
            auth_labels = {"api_key":"API Key","feishu_app":"飞书","openclaw_bot":"OpenClaw"}
            st.caption(f"认证: {auth_labels.get(auth_m, auth_m)}")

            st.metric("Credits", f"{a.get('credit_balance',0):.1f}")
            if st.session_state.api_key:
                st.caption(f"API: {st.session_state.api_key[:16]}...")

            # 充值入口
            with st.expander("Deposit / 充值"):
                eth_addr = a.get("eth_address", "")
                st.caption(f"钱包地址: {eth_addr[:12]}...{eth_addr[-6:]}" if eth_addr else "无钱包")
                st.markdown(f"<div style='font-size:0.7rem;color:var(--text-secondary);word-break:break-all'>{eth_addr}</div>", unsafe_allow_html=True)

                st.markdown("**生产方式**：用 MetaMask 向上述地址转账 ETH，然后调 API 凭 tx_hash 入账")
                st.markdown("**演示模式**：Hardhat 测试币一键充值👇")

                dep_amount = st.number_input("充值 (credits)", 10, 500, 50, 10, key="deposit_amt")
                if st.button("充值 (测试币)", type="primary", use_container_width=True, key="deposit_btn"):
                    r = api_post("/api/billing/deposit", {"amount_credits": dep_amount})
                    if r.get("error"):
                        st.error(r.get("detail"))
                    else:
                        st.success(f"+{dep_amount} credits ✅")
                        if r.get("chain_tx_hash"):
                            st.caption(f"链上 tx: {r['chain_tx_hash'][:14]}...")
                        refresh_me()
                        st.rerun()

            if st.button("退出", use_container_width=True):
                for k in ["logged_in","agent","api_key","counterparty"]:
                    st.session_state[k] = {} if k in ("agent","counterparty") else (False if k=="logged_in" else "")
                st.rerun()

        st.divider()

        # 交易对手快捷方式
        cp = st.session_state.counterparty
        if cp:
            st.caption("交易对手")
            st.markdown(f"{cp.get('avatar','')} **{cp.get('name','')}**")
            st.markdown(f'<span class="tag {"tag-provider" if cp.get("role")=="provider" else "tag-consumer"}">{cp.get("role")}</span>', unsafe_allow_html=True)
        else:
            st.caption("尚未选择交易对手")

        st.divider()
        if st.button("清空日志", use_container_width=True):
            st.session_state.logs = []
            st.rerun()


# ═══════ 页面 1: Agent 市场 ═══════
def page_market():
    st.markdown('<p class="app-title">Agent 市场</p>', unsafe_allow_html=True)
    st.markdown('<p class="app-subtitle" style="color:#64748b;font-size:0.9rem">浏览已注册的 Provider/Consumer，选择你要交易的对手方</p>', unsafe_allow_html=True)

    if not st.session_state.logged_in:
        st.warning("⚠️ 请先在左侧边栏登录，登录后才能发起交易")

    # 当前交易对手
    cp = st.session_state.counterparty
    if cp:
        role_tag = "tag-provider" if cp.get("role")=="provider" else "tag-consumer"
        st.markdown(f"""
        <div class="card card-accent">
            <b>🎯 当前交易对手：</b>{cp.get('avatar','🤖')} <b>{cp.get('name')}</b>
            <span class="tag {role_tag}">{cp.get('role')}</span>
            {'<span class="agent-meta">服务: '+cp.get('service_name','')+' · '+str(cp.get('price_per_call',0))+' credits/次</span>' if cp.get('role')=='provider' else ''}
        </div>""", unsafe_allow_html=True)

    # 获取 Agent 列表
    all_data = api_get("/api/agents/all")
    agents = all_data.get("agents", []) if not all_data.get("error") else []
    me_id = st.session_state.agent.get("agent_id", "")

    # 排除自己
    providers = [a for a in agents if a.get("role")=="provider" and a.get("is_service_active") and a["agent_id"] != me_id]
    consumers = [a for a in agents if a.get("role")=="consumer" and a["agent_id"] != me_id]

    col_p, col_c = st.columns(2)

    with col_p:
        st.markdown(f"**Provider 服务方 ({len(providers)})**")
        if not providers:
            st.caption("暂无 Provider 在线")
        for a in providers:
            is_cp = cp and cp.get("agent_id") == a["agent_id"]
            border = "2px solid #1a73e8" if is_cp else "1px solid #e2e8f0"
            bg = "#eff6ff" if is_cp else "white"
            st.markdown(f"""
            <div class="card" style="border:{border};background:{bg}">
                <b>{a.get('avatar','🤖')} {a['name']}</b>
                <span class="tag tag-provider">Provider</span>
                <div class="agent-meta">{a.get('service_name','-') or '-'} · {a.get('price_per_call',0)} credits/次</div>
                <div class="agent-meta">{a.get('service_description','')[:60]}</div>
            </div>""", unsafe_allow_html=True)
            # 单独一行放置按钮，更显眼
            if is_cp:
                st.success(f"当前对手: {a['name']}")
            else:
                if st.button(f"选择此 Provider 交易", key=f"trade_p_{a['agent_id']}", type="primary", use_container_width=True):
                    st.session_state.counterparty = a
                    add_log(f"🎯 已选择对手: {a['name']} (Provider)", "success")
                    st.rerun()

    with col_c:
        st.markdown(f"**Consumer 消费方 ({len(consumers)})**")
        if not consumers:
            st.caption("暂无 Consumer 在线")
        for a in consumers:
            is_cp = cp and cp.get("agent_id") == a["agent_id"]
            border = "2px solid #1a73e8" if is_cp else "1px solid #e2e8f0"
            bg = "#eff6ff" if is_cp else "white"
            st.markdown(f"""
            <div class="card" style="border:{border};background:{bg}">
                <b>{a.get('avatar','🤖')} {a['name']}</b>
                <span class="tag tag-consumer">Consumer</span>
                <div class="agent-meta">{a.get('description','')[:60]}</div>
            </div>""", unsafe_allow_html=True)
            if is_cp:
                st.success(f"当前对手: {a['name']}")
            else:
                if st.button(f"选择此 Consumer 交易", key=f"trade_c_{a['agent_id']}", type="primary", use_container_width=True):
                    st.session_state.counterparty = a
                    add_log(f"🎯 已选择对手: {a['name']} (Consumer)", "success")
                    st.rerun()


# ═══════ 页面 2: 交易看板 ═══════
def page_trade():
    st.markdown('<p class="app-title">交易看板</p>', unsafe_allow_html=True)

    cp = st.session_state.counterparty
    a = st.session_state.agent

    if not a or not cp:
        # 未登录也展示公开交易流
        st.info("登录后可查看完整交易看板。以下展示公开交易流水：" if not a else "请先选择交易对手")

    # ── 双轨结算说明 ──
    st.markdown("""
    <div class="card card-accent">
        <b>🔗 双轨结算机制 (Dual-Track Settlement)</b><br>
        <span style="color:#64748b;font-size:0.85rem;">
        <b>Track 1 - Off-chain Credits (SQLite):</b> 快速记账，前端展示余额<br>
        <b>Track 2 - On-chain ETH (EscrowPayment 合约):</b> Consumer 锁定 ETH 到合约 → Provider 交付 → Consumer 确认 → 合约释放 ETH<br>
        链上交易提供<b>去中心化保证</b>：任何一方都无法单方面挪用资金。
        </span>
    </div>""", unsafe_allow_html=True)

    # ── 流程可视化 ──
    st.markdown('<p class="section-title">🌐 去中心化交易流程</p>', unsafe_allow_html=True)

    # 确定谁是谁
    if a.get("role") == "consumer":
        consumer_name = a.get("name","我")
        consumer_avatar = a.get("avatar","🤖")
        provider_name = cp.get("name","对手")
        provider_avatar = cp.get("avatar","🤖")
    else:
        provider_name = a.get("name","我")
        provider_avatar = a.get("avatar","🤖")
        consumer_name = cp.get("name","对手")
        consumer_avatar = cp.get("avatar","🤖")

    c1, a1, c2, a2, c3 = st.columns([2, 0.3, 2, 0.3, 2])
    with c1:
        st.markdown(f'<div class="flow-node flow-consumer">{consumer_avatar} {consumer_name}<br><small>Consumer</small></div>', unsafe_allow_html=True)
    with a1:
        st.markdown('<div style="text-align:center;color:#94a3b8;padding-top:15px;font-size:1.2rem">→</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="flow-node flow-contract">📜 智能合约<br><small>EscrowPayment</small></div>', unsafe_allow_html=True)
    with a2:
        st.markdown('<div style="text-align:center;color:#22c55e;padding-top:15px;font-size:1.2rem">→</div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="flow-node flow-provider">{provider_avatar} {provider_name}<br><small>Provider</small></div>', unsafe_allow_html=True)

    # 交易记录
    st.divider()
    st.markdown('<p class="section-title">📋 交易记录</p>', unsafe_allow_html=True)

    txs = api_get("/api/billing/feed", {"limit": 30}).get("transactions", [])

    if not txs:
        st.info("暂无交易记录")
    else:
        for tx in txs[:20]:
            s = tx.get("status","pending")
            labels = {"pending":"⏳","fund_locked":"🔒","delivered":"📦","confirmed":"✅","disputed":"⚠️","refunded":"↩️","failed":"❌"}
            hash_badge = f'<span class="chain-badge">🔗 {tx.get("chain_tx_hash","")[:10]}...</span>' if tx.get("chain_tx_hash") else '<span class="no-chain-badge">off-chain</span>'
            st.markdown(f"""
            <div style="display:flex;align-items:center;gap:8px;padding:6px 10px;margin:2px 0;background:white;border-radius:8px;border:1px solid #e2e8f0;font-size:0.82rem">
                <span style="color:#94a3b8;min-width:45px">{tx.get('created_at','')[:16]}</span>
                <b>{tx.get('consumer_name','?')}</b><span style="color:#94a3b8">→</span><b>{tx.get('provider_name','?')}</b>
                <span style="color:#1a73e8;font-weight:700">{tx.get('amount',0):.0f}cr</span>
                <span>{labels.get(s,s)}</span>
                {hash_badge}
                <span style="color:#94a3b8;font-size:0.7rem">{tx.get('service_name','')}</span>
            </div>""", unsafe_allow_html=True)

    # 平台统计
    st.divider()
    stats = api_get("/api/billing/stats")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(f'<div class="metric-box"><div class="metric-val">{stats.get("total_agents",0)}</div><div class="metric-lbl">Agent</div></div>', unsafe_allow_html=True)
    with m2:
        st.markdown(f'<div class="metric-box"><div class="metric-val">{stats.get("total_transactions",0)}</div><div class="metric-lbl">交易</div></div>', unsafe_allow_html=True)
    with m3:
        st.markdown(f'<div class="metric-box"><div class="metric-val">{stats.get("confirmed_transactions",0)}</div><div class="metric-lbl">已完成</div></div>', unsafe_allow_html=True)
    with m4:
        st.markdown(f'<div class="metric-box"><div class="metric-val">{stats.get("total_volume_credits",0):.0f}</div><div class="metric-lbl">交易额(cr)</div></div>', unsafe_allow_html=True)


# ═══════ 页面 3: 执行交易 ═══════
def page_execute():
    st.markdown('<p class="app-title">执行交易</p>', unsafe_allow_html=True)

    if not st.session_state.logged_in:
        st.info("请先登录")
        return

    a = st.session_state.agent
    cp = st.session_state.counterparty

    if not cp:
        st.info("请先在 Agent 市场选择交易对手")
        # 快捷导航
        st.button("→ 去 Agent 市场选择对手", on_click=lambda: None)
        return

    # 身份确认
    col1, col2 = st.columns(2)
    with col1:
        cls = "tag-provider" if a.get("role")=="provider" else "tag-consumer"
        st.markdown(f'<div class="card"><b>{a.get("avatar","🤖")} 我: {a.get("name")}</b> <span class="tag {cls}">{a.get("role")}</span><br>余额: <b>{a.get("credit_balance",0):.1f} credits</b></div>', unsafe_allow_html=True)
    with col2:
        cp_cls = "tag-provider" if cp.get("role")=="provider" else "tag-consumer"
        cp_extra = f"<br>服务: {cp.get('service_name','')} · {cp.get('price_per_call',0)} credits" if cp.get("role")=="provider" else ""
        st.markdown(f'<div class="card"><b>{cp.get("avatar","🤖")} 对手: {cp.get("name")}</b> <span class="tag {cp_cls}">{cp.get("role")}</span>{cp_extra}</div>', unsafe_allow_html=True)

    st.divider()

    if a.get("role") == "consumer":
        # Consumer 支付流程
        if cp.get("role") != "provider":
            st.warning("你是 Consumer，请选择一个 Provider 作为交易对手")
            return

        st.markdown('<p class="section-title">💳 发起支付 (Consumer)</p>', unsafe_allow_html=True)
        with st.form("pay"):
            amount = st.number_input("支付金额 (credits)", 1.0, a.get("credit_balance",0), float(cp.get("price_per_call",5)), 1.0)
            st.caption(f"≈ {amount * 0.01:.4f} ETH (将锁定在 EscrowPayment 合约中)")
            if st.form_submit_button("🔒 锁定资金 → 上链", type="primary", use_container_width=True):
                r = api_post("/api/billing/pay", {"provider_id": cp["agent_id"], "amount": amount})
                if r.get("error"):
                    st.error(r.get("detail"))
                else:
                    add_log(f"🔒 锁定 {amount} credits → {cp['name']}", "success")
                    if r.get("chain_tx_hash"):
                        add_log(f"🔗 链上交易: {r['chain_tx_hash'][:16]}...", "success")
                    st.success(r.get("message",""))
                    # 自动保存交易ID到 session，下方一键确认
                    st.session_state.last_tx_id = r.get("transaction_id", "")
                    st.session_state.last_tx_amount = amount
                    refresh_me()
                    st.rerun()

        # 确认交付 (一键确认，无需填交易ID)
        st.divider()
        st.markdown('<p class="section-title">✅ 确认交付 → 释放资金</p>', unsafe_allow_html=True)
        if st.session_state.get("last_tx_id"):
            tx_id = st.session_state.last_tx_id
            st.markdown(f"""
            <div class="card" style="background:var(--primary-bg);border:1px solid var(--primary-border)">
                <div style="font-size:0.78rem;color:var(--text-secondary)">待确认交易</div>
                <div style="font-family:monospace;font-size:0.9rem;color:var(--primary);margin-top:4px">{tx_id}</div>
                <div style="font-size:0.78rem;color:var(--text-secondary);margin-top:4px">金额: {st.session_state.get("last_tx_amount", 0)} credits</div>
            </div>
            """, unsafe_allow_html=True)
            if st.button("✅ 确认释放资金", type="primary", use_container_width=True):
                r = api_post(f"/api/billing/confirm/{tx_id}")
                if r.get("error"):
                    st.error(r.get("detail"))
                else:
                    add_log("✅ 已确认！资金释放给 Provider", "success")
                    if r.get("chain_confirm_hash"):
                        add_log(f"🔗 链上确认: {r['chain_confirm_hash'][:16]}...", "success")
                    st.session_state.last_tx_id = ""  # 清空，下次重新填
                    st.session_state.last_tx_amount = 0
                    refresh_me()
                    st.rerun()

    else:
        # Provider 执行流程
        if cp.get("role") != "consumer":
            st.warning("你是 Provider，请选择一个 Consumer 作为交易对手")
            return

        st.markdown('<p class="section-title">🛠️ 执行服务 (Provider)</p>', unsafe_allow_html=True)
        with st.form("exec"):
            task_type = st.selectbox("任务类型", ["data_query","api_call","desktop_automation","content_generation"])
            target = st.text_input("任务目标", placeholder="例: 查询 AAPL 今日收盘价")
            charge = st.number_input("收费 (credits)", 0.0, 1000.0, float(a.get("price_per_call",5)), 1.0)
            if task_type == "desktop_automation":
                st.info("💻 将通过 CUA-driver 操控 macOS 桌面自动执行")
            if st.form_submit_button("⚙️ 执行 & 自动计费", type="primary", use_container_width=True):
                r = api_post("/api/openclaw/execute", {
                    "task_type": task_type, "target": target,
                    "consumer_id": cp["agent_id"], "charge_amount": charge,
                })
                if r.get("error"):
                    st.error(r.get("detail"))
                else:
                    add_log(f"⚙️ 执行完成: {target} → 收费 {charge} credits", "success")
                    st.success(r.get("message",""))
                    refresh_me()
                    st.rerun()


# ═══════ 页面 4: 实时演示 (Live Demo) ═══════
def page_live():
    """实时演示：任意 Agent → AgentPay → 看板联动，展示最新触发的交易流。
    使用顶部导航栏手动切换页面。"""
    st.markdown('<p class="app-title">实时演示</p>', unsafe_allow_html=True)
    st.markdown('<p class="app-subtitle" style="color:var(--text-secondary);font-size:0.85rem">任意 Agent 发起交易 → 链上锁定 → 自动结算 → 看板实时联动</p>', unsafe_allow_html=True)

    # ── 流程说明 ──
    st.markdown(f"""
    <div class="card" style="background:linear-gradient(135deg,#f5f3ff,#ede9fe);text-align:center;border-color:#ddd6fe">
        <b style="color:#5b21b6">交易流程</b><br>
        <span style="font-size:1em;color:#4c1d95">
            Consumer 发起 → 身份验证 → 链上锁定 ETH → Provider 执行 → 确认 → 看板更新
        </span>
    </div>
    """, unsafe_allow_html=True)

    # ── 实时数据 (用公开接口，不登录也能看) ──
    txs = api_get("/api/billing/feed", {"limit": 20}).get("transactions", [])
    agents = api_get("/api/agents/all").get("agents", [])

    # 最新一笔交易
    if txs:
        latest = txs[0]
        # 动态获取头像 (不硬编码)
        consumer_avatar = "🤖"
        provider_avatar = "🤖"
        for a in agents:
            if a.get("name") == latest.get("consumer_name"):
                consumer_avatar = a.get("avatar", "🤖") or "🤖"
            if a.get("name") == latest.get("provider_name"):
                provider_avatar = a.get("avatar", "🤖") or "🤖"

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f'<div class="metric-box"><div class="metric-val" style="color:#7c3aed">{consumer_avatar} {latest["consumer_name"]}</div><div class="metric-lbl">Consumer</div></div>', unsafe_allow_html=True)
        with col2:
            st.markdown(f'<div class="metric-box"><div class="metric-val" style="color:#6d28d9">{provider_avatar} {latest["provider_name"]}</div><div class="metric-lbl">Provider</div></div>', unsafe_allow_html=True)
        with col3:
            st.markdown(f'<div class="metric-box"><div class="metric-val">{latest["amount"]:.0f}</div><div class="metric-lbl">💳 Credits</div></div>', unsafe_allow_html=True)
        with col4:
            chain_hash = latest.get("chain_tx_hash", "")
            chain_label = f'<span class="chain-badge">{chain_hash[:10]}...</span>' if chain_hash else '<span class="no-chain-badge">⏳ off-chain</span>'
            st.markdown(f'<div class="metric-box"><div class="metric-val" style="font-size:0.9em">{chain_label}</div><div class="metric-lbl">🔗 链上</div></div>', unsafe_allow_html=True)

        # 流程图 (通用化)
        st.markdown("### 🔄 最新交易流向")
        c1, a1, c2, a2, c3 = st.columns([2, 0.3, 2, 0.3, 2])
        with c1:
            st.markdown(f'<div class="flow-node flow-consumer">{consumer_avatar} {latest["consumer_name"]}<br><small>Consumer</small></div>', unsafe_allow_html=True)
        with a1:
            st.markdown('<div style="text-align:center;color:#94a3b8;padding-top:15px;font-size:1.2rem">→</div>', unsafe_allow_html=True)
        with c2:
            st.markdown('<div class="flow-node flow-contract">📜 EscrowPayment<br><small>链上智能合约</small></div>', unsafe_allow_html=True)
        with a2:
            status_emoji = "✅" if latest["status"] == "confirmed" else "🔒" if latest["status"] == "fund_locked" else "⏳"
            st.markdown(f'<div style="text-align:center;color:#22c55e;padding-top:15px;font-size:1.2rem">{status_emoji}</div>', unsafe_allow_html=True)
        with c3:
            st.markdown(f'<div class="flow-node flow-provider">{provider_avatar} {latest["provider_name"]}<br><small>Provider</small></div>', unsafe_allow_html=True)

    # 余额对比
    st.markdown("### 当前余额")
    me_provider = next((a for a in agents if a.get("role") == "provider"), None)
    me_consumer = next((a for a in agents if a.get("role") == "consumer"), None)
    p_col, c_col = st.columns(2)
    with p_col:
        if me_provider:
            provider_balance = me_provider.get('total_earned', 0) - me_provider.get('total_spent', 0)
            st.markdown(f"""
            <div class="card" style="border-left:3px solid var(--primary)">
                <b>{me_provider['name']} (Provider)</b><br>
                <span style="font-size:1.5em;color:#6d28d9">{provider_balance:.0f}</span> credits 余额<br>
                <span style="color:var(--text-secondary)">已收入: {me_provider.get('total_earned',0):.0f} · 服务: {me_provider.get('service_name','-')}</span>
            </div>
            """, unsafe_allow_html=True)
    with c_col:
        if me_consumer:
            consumer_balance = me_consumer.get('total_earned', 0) - me_consumer.get('total_spent', 0)
            consumer_spent = me_consumer.get('total_spent', 0)
            st.markdown(f"""
            <div class="card" style="border-left:3px solid #c4b5fd">
                <b>{me_consumer['name']} (Consumer)</b><br>
                <span style="font-size:1.5em;color:#6d28d9">{consumer_balance:.0f}</span> credits 余额<br>
                <span style="color:var(--text-secondary)">已支出: {consumer_spent:.0f} · 已调用: {len(txs)} 次</span>
            </div>
            """, unsafe_allow_html=True)

    # 实时交易流水
    st.markdown("### 📋 实时交易流水")
    if txs:
        for tx in txs[:10]:
            s = tx.get("status","")
            labels = {"pending":"⏳","fund_locked":"🔒","delivered":"📦","confirmed":"✅","disputed":"⚠️","refunded":"↩️","failed":"❌"}
            hash_badge = f'<span class="chain-badge">🔗 {tx.get("chain_tx_hash","")[:10]}...</span>' if tx.get("chain_tx_hash") else '<span class="no-chain-badge">off-chain</span>'
            st.markdown(f"""
            <div style="display:flex;align-items:center;gap:8px;padding:6px 10px;margin:2px 0;background:white;border-radius:8px;border:1px solid #e2e8f0;font-size:0.82rem">
                <span style="color:#94a3b8;min-width:130px">{tx.get('created_at','')}</span>
                <b>{tx.get('consumer_name','?')}</b><span style="color:#94a3b8">→</span><b>{tx.get('provider_name','?')}</b>
                <span style="color:#1a73e8;font-weight:700">{tx.get('amount',0):.0f}cr</span>
                <span>{labels.get(s,s)}</span>
                {hash_badge}
                <span style="color:#94a3b8;font-size:0.7rem">{tx.get('service_name','')}</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("💡 还没有任何交易记录")


# ═══════ 页面 5: 日志 ═══════
def page_logs():
    st.markdown('<p class="app-title">日志</p>', unsafe_allow_html=True)

    icons = {"info":"📝","success":"✅","error":"❌","warning":"⚠️"}
    for log in st.session_state.logs[-50:]:
        icon = icons.get(log["level"], "📝")
        st.markdown(f"""
        <div class="log-entry">
            <span class="log-time">{log["time"]}</span>
            <span>{icon}</span>
            <span class="log-msg">{log["msg"]}</span>
        </div>
        """, unsafe_allow_html=True)


# ═══════ 顶部按钮导航 ═══════
def top_nav():
    """顶部按钮式导航条 (替代 tabs)"""
    st.markdown("""
    <style>
    .nav-bar { background: var(--card); padding: 10px 16px; border-radius: 10px; margin-bottom: 12px;
              box-shadow: 0 1px 2px rgba(109,40,217,0.04); border: 1px solid var(--border); }
    </style>
    """, unsafe_allow_html=True)

    pages = [
        ("live",    "实时演示"),
        ("market",  "Agent 市场"),
        ("trade",   "交易看板"),
        ("execute", "执行交易"),
        ("logs",    "日志"),
    ]
    # 比例: logo 2.5, 每个按钮 1.5, 末尾留白 1
    cols = st.columns([2.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1])
    with cols[0]:
        st.markdown(f'<span style="font-weight:700;color:var(--primary);font-size:1.25rem;white-space:nowrap">AgentPay</span>', unsafe_allow_html=True)
    for i, (key, label) in enumerate(pages):
        with cols[i + 1]:
            is_active = st.session_state.get("current_page") == key
            btn_type = "primary" if is_active else "secondary"
            if st.button(label, key=f"nav_{key}", type=btn_type, use_container_width=True):
                st.session_state.current_page = key
                st.rerun()
    st.markdown("---")


# ═══════ 入口 ═══════
def main():
    if "current_page" not in st.session_state:
        st.session_state.current_page = "live"

    sidebar()
    top_nav()

    page = st.session_state.current_page
    if page == "live":    page_live()
    elif page == "market":  page_market()
    elif page == "trade":   page_trade()
    elif page == "execute": page_execute()
    elif page == "logs":    page_logs()


if __name__ == "__main__":
    main()
