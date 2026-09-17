"""
AgentFleet — 侧边栏（老板控制台）
================================
一人公司：只有老板（人）登录。老板登录自己的账号 → 创建名下 agent → 充值 → 查余额。
agent 的 api_key 是 worker 运行时的内部凭证，不在控制台暴露登录入口。
"""

import streamlit as st

from ui.api import api_get, api_post
from ui.session import add_log, refresh_me
from ui.components import role_tag, avatar_badge

# 与 backend/config.py 保持一致（演示账号）
DEMO_BOSS_PHONE = "13800000000"
DEMO_BOSS_PASSWORD = "demo123456"
CNY_PER_CREDIT = 0.1  # 1 credit = 0.1 元（CNY_TO_CREDIT_RATE = 10 的倒数）


def render_sidebar():
    with st.sidebar:
        st.markdown('<p class="app-title" style="font-size:1.3rem">AgentFleet</p>', unsafe_allow_html=True)
        st.caption("一人公司 · 老板控制台")

        if not st.session_state.logged_in:
            _render_boss_login()
        else:
            _render_boss_profile()

        st.divider()
        if st.button("清空日志", width="stretch"):
            st.session_state.logs = []
            st.rerun()


# ── 登录 ──

def _apply_login(r: dict):
    """登录/注册成功后写入 session_state。"""
    st.session_state.api_key = r.get("api_key", "")
    st.session_state.agent = r
    st.session_state.logged_in = True
    refresh_me()  # 拉取 eth_address / 余额等完整字段
    add_log(f"👋 欢迎回来，老板 {r.get('name','')}！", "success")


def _demo_login():
    r = api_post("/api/boss/login", {"phone": DEMO_BOSS_PHONE, "password": DEMO_BOSS_PASSWORD})
    if r.get("error"):
        # 后端尚未播种演示账号 → 先注册再登录
        reg = api_post("/api/boss/register", {
            "name": "演示老板", "phone": DEMO_BOSS_PHONE, "password": DEMO_BOSS_PASSWORD,
        })
        if reg.get("error"):
            st.error(reg.get("detail", "演示登录失败"))
            return
        r = api_post("/api/boss/login", {"phone": DEMO_BOSS_PHONE, "password": DEMO_BOSS_PASSWORD})
    if r.get("error"):
        st.error(r.get("detail", "演示登录失败"))
        return
    _apply_login(r)
    st.rerun()


def _render_boss_login():
    st.markdown("**老板登录**")
    mode = st.segmented_control(
        "登录方式", ["登录", "注册"], default="登录", label_visibility="collapsed"
    ) or "登录"

    if mode == "注册":
        with st.form("boss_register"):
            name = st.text_input("老板称呼", placeholder="例: 王总")
            phone = st.text_input("手机号", placeholder="13800000000")
            password = st.text_input("密码", type="password", placeholder="至少 6 位")
            if st.form_submit_button("注册老板账号", type="primary", width="stretch"):
                r = api_post("/api/boss/register", {"name": name, "phone": phone, "password": password})
                if r.get("error"):
                    st.error(r.get("detail", "注册失败"))
                else:
                    _apply_login(r)
                    st.rerun()
    else:
        with st.form("boss_login"):
            phone = st.text_input("手机号", placeholder="13800000000")
            password = st.text_input("密码", type="password", placeholder="••••••••")
            if st.form_submit_button("登录", type="primary", width="stretch"):
                r = api_post("/api/boss/login", {"phone": phone, "password": password})
                if r.get("error"):
                    st.error("手机号或密码错误")
                else:
                    _apply_login(r)
                    st.rerun()

        if st.button("⚡ 一键演示登录", width="stretch"):
            _demo_login()


# ── 老板资料 + 余额 ──

def _render_boss_profile():
    a = st.session_state.agent
    st.success("已登录")
    st.markdown(
        f"{avatar_badge(a.get('avatar','👔'), a.get('avatar_color', '#3370ff'))} "
        f"**{a.get('name','')}**",
        unsafe_allow_html=True,
    )
    st.markdown(role_tag("boss"), unsafe_allow_html=True)
    if a.get("phone"):
        st.caption(f"手机号: {a.get('phone')}")

    st.divider()

    # 余额卡——只展示 credits/人民币；链上 ETH 那栏一直显示 0.0000（demo 环境没接链上节点，
    # 见 backend/chain.py 的 chain_available()），光占位不传达信息，先从展示上拿掉，
    # 链上结算这套代码/合约本身没删，接了真节点随时能用。
    bal = api_get("/api/billing/balance")
    credits = bal.get("credit_balance", a.get("credit_balance", 0))
    st.markdown("**余额**")
    st.metric("Credits", f"{credits:.1f}")
    st.metric("折合 ¥", f"{credits * CNY_PER_CREDIT:.1f}")

    st.caption("💳 充值请点顶部导航「充值」页")

    st.divider()
    with st.expander("🔐 账号管理"):
        with st.form("change_password"):
            old_pw = st.text_input("旧密码", type="password")
            new_pw = st.text_input("新密码（至少 6 位）", type="password")
            new_pw2 = st.text_input("确认新密码", type="password")
            if st.form_submit_button("修改密码", width="stretch"):
                if not old_pw or not new_pw:
                    st.error("旧密码、新密码都要填")
                elif new_pw != new_pw2:
                    st.error("两次新密码没对上")
                elif len(new_pw) < 6:
                    st.error("新密码至少 6 位")
                else:
                    r = api_post("/api/boss/change-password", {
                        "old_password": old_pw, "new_password": new_pw,
                    })
                    if r.get("error"):
                        st.error(r.get("detail", "修改失败"))
                    else:
                        add_log("🔐 密码已修改", "success")
                        st.success("密码已修改，下次登录用新密码")

    st.divider()
    if st.button("退出", width="stretch"):
        from ui.session import reset_auth
        reset_auth()
        st.rerun()
