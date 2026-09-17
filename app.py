"""
AgentFleet — Streamlit 前端入口
================================
一人公司经营台：CEO 视角看智能体群自主协作，支付只是结算层

模块化结构：
  app_pages/   各页面（直接脚本）
  ui/          共享逻辑（主题 / API 客户端 / 会话 / 组件 / 侧边栏）

增删页面：只需在下方 st.navigation 列表中加/删一行 st.Page，并新增/删除 app_pages/ 里的对应文件。
"""

import streamlit as st

from ui.theme import inject_css
from ui.session import init_session
from ui.sidebar import render_sidebar

st.set_page_config(page_title="AgentFleet", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

inject_css()
init_session()
render_sidebar()

page = st.navigation(
    [
        st.Page("app_pages/live.py", title="经营台", icon=":material/dashboard:", default=True),
        st.Page("app_pages/recharge.py", title="充值", icon=":material/payments:"),
        st.Page("app_pages/team.py", title="我的 Agent", icon=":material/groups:"),
        st.Page("app_pages/skills.py", title="我的技能", icon=":material/bolt:"),
        st.Page("app_pages/trade.py", title="账本", icon=":material/account_balance:"),
    ],
    position="top",
)

page.run()
