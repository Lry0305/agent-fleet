"""
AgentFleet — 主题 & 样式
========================
中性蓝灰主题（card / badge / flow-node / metric / log）。
与 .streamlit/config.toml 的原生主题配合使用。
"""

import streamlit as st

_CSS = """
<style>
    /* === 统一配色（飞书风：白底 + 蓝色交互元素，绿色只做点缀） === */
    :root {
        --primary: #3370ff;
        --primary-light: #588cff;
        --primary-bg: #f0f5ff;
        --primary-border: #d6e4ff;
        --bg: #ffffff;
        --card: #ffffff;
        --border: #e5e6eb;
        --text: #1f2329;
        --text-secondary: #646a73;
        --success: #2ba471;
        --success-bg: #e8f8f0;
        --warning: #d97706;
        --warning-bg: #fffbeb;
        --danger: #dc2626;
        --danger-bg: #fef2f2;
    }
    * { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
    .stApp { background: var(--bg); }
    .block-container { max-width: 1280px; padding: 1.5rem 2rem; }
    .app-title { font-size: 1.5rem; font-weight: 700; color: var(--text); margin: 0; }
    .section-title { font-size: 1rem; font-weight: 600; color: var(--text); margin: 8px 0; }
    .card { background: var(--card); border-radius: 10px; padding: 16px;
            box-shadow: 0 1px 2px rgba(15,23,42,0.04); border: 1px solid var(--border); margin-bottom: 10px; }
    .card-accent { border-left: 3px solid var(--primary); }
    .tag { font-size: 0.68rem; font-weight: 600; padding: 2px 8px; border-radius: 4px; display: inline-block; }
    .tag-provider { background: var(--primary-bg); color: var(--primary); }
    .tag-consumer { background: #f1f5f9; color: #475569; }
    .tag-boss { background: var(--warning-bg); color: var(--warning); }
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
    .flow-contract { background: #f8fafc; border: 2px dashed var(--primary-light); color: var(--text-secondary); }
    .flow-provider { background: var(--primary-bg); border-color: var(--primary-light); color: var(--primary); }
    .chain-badge { background: var(--primary-bg); color: var(--primary); font-size: 0.7rem;
                   padding: 2px 6px; border-radius: 4px; font-family: monospace;
                   border: 1px solid var(--primary-border); }
    .no-chain-badge { background: #f8fafc; color: var(--text-secondary); font-size: 0.7rem;
                      padding: 2px 6px; border-radius: 4px; border: 1px solid var(--border); }
    .stButton>button { border-radius: 6px; font-weight: 500; }
    /* 强制所有 primary 按钮为主色 (覆盖 Streamlit 默认红/绿) */
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
        background-color: #265ed1 !important;
        border-color: #265ed1 !important;
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
    /* Streamlit 成功/错误框统一配色 */
    div[data-testid="stAlert"] { border-color: var(--primary-border) !important; }
    /* 日志卡片 */
    .log-entry { padding: 6px 12px; margin: 2px 0; border-radius: 6px; font-size: 0.8rem;
                 display: flex; align-items: center; gap: 8px; }
    .log-entry:hover { background: var(--primary-bg); }
    .log-time { color: var(--text-secondary); min-width: 60px; font-size: 0.75rem; }
    .log-msg { color: var(--text); }

    /* === 聊天气泡（协议消息流 / 会话日志） === */
    .chat-thread { display: flex; flex-direction: column; gap: 10px; padding: 4px 2px; }
    .chat-row { display: flex; gap: 8px; align-items: flex-end; }
    .chat-row.chat-right { flex-direction: row-reverse; }
    .chat-avatar { font-size: 1.2rem; width: 30px; height: 30px; border-radius: 50%;
                   background: var(--primary-bg); display: flex; align-items: center;
                   justify-content: center; flex-shrink: 0; }
    .chat-bubble-wrap { display: flex; flex-direction: column; max-width: 72%; }
    .chat-row.chat-right .chat-bubble-wrap { align-items: flex-end; }
    .chat-meta { font-size: 0.7rem; color: var(--text-secondary); margin: 0 4px 2px; }
    .chat-bubble { padding: 8px 12px; border-radius: 12px; font-size: 0.82rem; line-height: 1.45;
                   border: 1px solid var(--border); background: var(--card); color: var(--text); }
    .chat-row.chat-right .chat-bubble {
        background: var(--primary); color: white; border-color: var(--primary);
    }
    .chat-bubble .chat-type-badge {
        display: inline-block; font-size: 0.65rem; font-weight: 700; letter-spacing: 0.02em;
        padding: 1px 6px; border-radius: 4px; margin-bottom: 4px; text-transform: uppercase;
    }
    .chat-type-spawn, .chat-type-offer { background: var(--warning-bg); color: var(--warning); }
    .chat-type-accept, .chat-type-deliver, .chat-type-result { background: var(--success-bg); color: var(--success); }
    .chat-type-decline, .chat-type-error { background: var(--danger-bg); color: var(--danger); }
    .chat-type-status, .chat-type-ack { background: #f1f5f9; color: var(--text-secondary); }
    .chat-row.chat-right .chat-bubble .chat-type-badge { background: rgba(255,255,255,0.2); color: white; }
    .chat-payload { font-family: ui-monospace, monospace; font-size: 0.72rem; opacity: 0.85;
                    white-space: pre-wrap; word-break: break-word; margin-top: 2px; }
</style>
"""


def inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)
