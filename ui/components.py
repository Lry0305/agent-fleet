"""
AgentFleet — 可复用 UI 组件
===========================
把在各页面重复出现的 HTML 片段（标签 / 链上徽章 / 交易行 / 指标卡 / 流程图）收拢到这里。
"""

import streamlit as st

STATUS_LABELS = {
    "pending": "⏳",
    "fund_locked": "🔒",
    "delivered": "📦",
    "confirmed": "✅",
    "disputed": "⚠️",
    "refunded": "↩️",
    "failed": "❌",
}

ROLE_LABELS = {"provider": "执行 Agent", "consumer": "发起 Agent", "boss": "老板"}

# 头像预设组合：图案 + 配色成套给，不开放自选 emoji/自选颜色——每个选项本身就是一套搭配好的组合。
AVATAR_PRESETS = [
    ("🤖", "#3370ff", "机器人 · 蓝"),
    ("🧠", "#7c3aed", "大脑 · 紫"),
    ("🦾", "#0891b2", "机械臂 · 青"),
    ("🔍", "#16a34a", "研究员 · 绿"),
    ("📊", "#d97706", "分析师 · 橙"),
    ("✍️", "#db2777", "写手 · 粉"),
    ("🛠️", "#64748b", "工具人 · 灰"),
    ("⚡", "#ca8a04", "速度型 · 黄"),
]

JOB_STATUS_LABELS = {
    "draft": "📝", "running": "▶️", "paused": "⏸", "done": "✅", "cancelled": "⏹", "failed": "❌",
}
TASK_STATUS_LABELS = {
    "queued": "⏳", "assigned": "📮", "running": "▶️", "paused": "⏸",
    "done": "✅", "failed": "❌", "cancelled": "⏹",
}


def avatar_badge(emoji, color=None, size=28):
    """圆形头像徽章：图案 + 配色的组合，用半透明底色衬底（不是纯色块，跟卡片背景更融）。"""
    color = color or "#3370ff"
    return (
        f'<span style="display:inline-flex;align-items:center;justify-content:center;'
        f'width:{size}px;height:{size}px;min-width:{size}px;border-radius:50%;'
        f'background:{color}22;font-size:{size * 0.55:.0f}px;vertical-align:middle">{emoji}</span>'
    )


def tag(label, kind):
    """角色标签。kind: "provider" | "consumer" | "boss". """
    cls = {"provider": "tag-provider", "consumer": "tag-consumer", "boss": "tag-boss"}.get(kind, "tag-consumer")
    return f'<span class="tag {cls}">{label}</span>'


def role_tag(role):
    """角色标签：provider → 执行 Agent，consumer → 发起 Agent，boss → 老板。"""
    return tag(ROLE_LABELS.get(role, role), role)


def _status_badge(status, labels):
    color = {
        "running": "#3370ff", "paused": "#f59e0b", "done": "#2ba471",
        "cancelled": "#64748b", "failed": "#dc2626",
    }.get(status, "#64748b")
    emoji = labels.get(status, status)
    return f'<span style="color:{color};font-weight:600;white-space:nowrap">{emoji} {status}</span>'


def job_status_badge(status):
    return _status_badge(status, JOB_STATUS_LABELS)


def task_status_badge(status):
    return _status_badge(status, TASK_STATUS_LABELS)


def chain_badge(chain_hash):
    """有链上 hash → 紫色 🔗 徽章；否则 → off-chain 灰色徽章。"""
    if chain_hash:
        return f'<span class="chain-badge">🔗 {chain_hash[:10]}...</span>'
    return '<span class="no-chain-badge">off-chain</span>'


def metric_box(value_html, label):
    return (
        f'<div class="metric-box">'
        f'<div class="metric-val">{value_html}</div>'
        f'<div class="metric-lbl">{label}</div>'
        f"</div>"
    )


def tx_row(tx, full_time=False):
    """单条协作流水行。full_time=True 时展示完整时间戳（经营台页用）。"""
    s = tx.get("status", "pending")
    status = STATUS_LABELS.get(s, s)
    badge = chain_badge(tx.get("chain_tx_hash", ""))
    time_w = "130px" if full_time else "45px"
    time_txt = tx.get("created_at", "") if full_time else tx.get("created_at", "")[:16]
    return f"""
<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;margin:2px 0;background:white;border-radius:8px;border:1px solid #e2e8f0;font-size:0.82rem">
    <span style="color:#94a3b8;min-width:{time_w}">{time_txt}</span>
    <b>{tx.get('consumer_name','?')}</b><span style="color:#94a3b8">→</span><b>{tx.get('provider_name','?')}</b>
    <span style="color:#1a73e8;font-weight:700">{tx.get('amount',0):.0f}cr</span>
    <span>{status}</span>
    {badge}
    <span style="color:#94a3b8;font-size:0.7rem">{tx.get('service_name','')}</span>
</div>"""


def flow_diagram(
    consumer_name,
    consumer_avatar,
    provider_name,
    provider_avatar,
    contract_title="智能合约",
    contract_subtitle="EscrowPayment",
    status_emoji="→",
):
    """Consumer → 智能合约 → Provider 的三段式流程图。"""
    c1, a1, c2, a2, c3 = st.columns([2, 0.3, 2, 0.3, 2])
    with c1:
        st.markdown(
            f'<div class="flow-node flow-consumer">{consumer_avatar} {consumer_name}<br><small>发起 Agent</small></div>',
            unsafe_allow_html=True,
        )
    with a1:
        st.markdown(
            '<div style="text-align:center;color:#94a3b8;padding-top:15px;font-size:1.2rem">→</div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="flow-node flow-contract">📜 {contract_title}<br><small>{contract_subtitle}</small></div>',
            unsafe_allow_html=True,
        )
    with a2:
        st.markdown(
            f'<div style="text-align:center;color:#22c55e;padding-top:15px;font-size:1.2rem">{status_emoji}</div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f'<div class="flow-node flow-provider">{provider_avatar} {provider_name}<br><small>执行 Agent</small></div>',
            unsafe_allow_html=True,
        )
