"""
AgentFleet — 会话状态 & 日志
===========================
统一初始化 session_state，提供 add_log / refresh_me 等跨页共享能力。
"""

import streamlit as st
from datetime import datetime

from ui.api import api_get


def init_session():
    for k, v in {
        "logged_in": False, "agent": {}, "api_key": "", "logs": [],
        "counterparty": {}, "last_tx_id": "", "last_tx_amount": 0.0,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


def add_log(msg, level="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.append({"time": ts, "msg": msg, "level": level})
    st.session_state.logs = st.session_state.logs[-200:]


def refresh_me():
    """用 /api/agents/me 拉取当前账户的完整字段（avatar/服务/余额等）。"""
    me = api_get("/api/agents/me")
    if not me.get("error"):
        st.session_state.agent = me


def reset_auth():
    for k in ["logged_in", "agent", "api_key", "counterparty"]:
        if k in ("agent", "counterparty"):
            st.session_state[k] = {}
        elif k == "logged_in":
            st.session_state[k] = False
        else:
            st.session_state[k] = ""
