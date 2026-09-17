"""
账本页：老板视角的结算流水 + 群概览。
"""

import streamlit as st

from ui.api import api_get
from ui.components import metric_box, tx_row

st.markdown('<p class="app-title">账本</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="app-subtitle" style="color:var(--text-secondary);font-size:0.85rem">'
    "老板名下所有结算流水（锁仓 → 交付 → 确认）</p>",
    unsafe_allow_html=True,
)

# ── 群概览 ──
stats = api_get("/api/billing/stats")
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown(metric_box(stats.get("total_agents", 0), "Agent"), unsafe_allow_html=True)
with m2:
    st.markdown(metric_box(stats.get("total_transactions", 0), "协作"), unsafe_allow_html=True)
with m3:
    st.markdown(metric_box(stats.get("confirmed_transactions", 0), "已结算"), unsafe_allow_html=True)
with m4:
    st.markdown(metric_box(f'{stats.get("total_volume_credits", 0):.0f}', "成交额 (cr)"), unsafe_allow_html=True)

st.divider()
st.markdown('<p class="section-title">我的流水</p>', unsafe_allow_html=True)

if not st.session_state.get("logged_in"):
    st.info("登录后查看老板名下的结算流水")
else:
    txs = api_get("/api/billing/transactions", {"limit": 50}).get("transactions", [])
    if not txs:
        st.info("暂无流水 —— 派活并结算后这里会出现记录")
    else:
        for tx in txs[:30]:
            st.markdown(tx_row(tx, full_time=True), unsafe_allow_html=True)
