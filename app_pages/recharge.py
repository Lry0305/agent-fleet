"""
充值页：老板充值（支付宝 / 微信 / 银行卡）+ 余额可视化。
生产环境把「模拟支付成功」换成支付宝/微信的异步通知 webhook，数据结构不变。
"""

import streamlit as st

from ui.api import api_get, api_post
from ui.session import add_log, refresh_me
from ui.components import metric_box

CNY_PER_CREDIT = 0.1  # 与 ui/sidebar.py 一致
CHANNELS = {"alipay": "支付宝", "wechat": "微信支付", "card": "银行卡"}
CHANNEL_ICONS = {"alipay": "🅰️", "wechat": "💚", "card": "💳"}

st.markdown('<p class="app-title">充值</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="app-subtitle" style="color:var(--text-secondary);font-size:0.85rem">'
    "对接支付宝 / 微信 / 银行卡 · 到账即入 credits（1 元 = 10 credits）</p>",
    unsafe_allow_html=True,
)

if not st.session_state.logged_in:
    st.warning("⚠️ 请先在左侧边栏登录老板账号")
    st.stop()


def _balance_card():
    # 链上 ETH 那格一直是 0.0000（demo 环境没接链上节点），光占位不传达信息，从展示上拿掉；
    # 链上结算的代码/合约本身没删，backend/chain.py 接了真节点随时能用。
    bal = api_get("/api/billing/balance")
    credits = bal.get("credit_balance", 0)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(metric_box(f"{credits:.1f}", "Credits 余额"), unsafe_allow_html=True)
    with c2:
        st.markdown(metric_box(f"¥{credits * CNY_PER_CREDIT:.1f}", "折合人民币"), unsafe_allow_html=True)
    with c3:
        st.markdown(metric_box("🔗 已连接" if bal.get("chain_connected") else "⛔ 断链", "链状态"), unsafe_allow_html=True)


_balance_card()
st.divider()

# ── 发起充值 ──
left, right = st.columns([1, 1])

with left:
    st.markdown("### 发起充值")
    channel = st.segmented_control(
        "支付渠道", list(CHANNELS.keys()), default="alipay",
        format_func=lambda k: f"{CHANNEL_ICONS[k]} {CHANNELS[k]}",
    ) or "alipay"
    amount_cny = st.number_input("充值金额（元）", 1.0, 100000.0, 100.0, 10.0)
    est_credits = amount_cny / CNY_PER_CREDIT
    st.caption(f"到账约 {est_credits:.0f} credits")

    if st.button("生成支付", type="primary", width="stretch"):
        r = api_post("/api/boss/recharge", {"channel": channel, "amount_cny": amount_cny})
        if r.get("error"):
            st.error(r.get("detail", "下单失败"))
        else:
            st.session_state.recharge_order = r
            add_log(f"💳 创建充值订单 ¥{r['amount_cny']}（{r['channel_label']}）", "info")
            st.rerun()

def _cashier_header(order):
    ch = order["channel"]
    st.markdown(
        f"""
        <div class="card card-accent" style="text-align:center">
            <div style="font-size:0.8rem;color:var(--text-secondary)">{CHANNEL_ICONS.get(ch,'')} {order.get('channel_label','')} · 收银台</div>
            <div style="font-size:1.6rem;font-weight:700;margin:6px 0">¥{order['amount_cny']:.2f}</div>
            <div style="font-size:0.8rem;color:var(--text-secondary)">到账 {order['amount_credits']:.0f} credits</div>
            <div style="font-family:monospace;font-size:0.72rem;color:var(--text-secondary);margin-top:6px">订单: {order['order_id']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_qr_channel(order):
    """支付宝 / 微信：扫码支付。支付宝配了沙箱凭证时 pay_url 是真实签名链接，
    这里生成真能扫的二维码；没配（或微信——目前没接真实网关）就老实标"模拟"，不装真的。
    """
    ch = order["channel"]
    pay_url = order.get("pay_url", "")
    is_real = bool(order.get("is_real_gateway")) and pay_url.startswith("http")

    if is_real:
        try:
            import qrcode, io, base64
            img = qrcode.make(pay_url)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            st.markdown(
                f'<img src="data:image/png;base64,{b64}" width="160" '
                f'style="display:block;margin:14px auto;border-radius:8px">',
                unsafe_allow_html=True,
            )
            st.caption("支付宝沙箱买家 App 扫码，或点下面按钮在浏览器打开付款页（真实签名链接，不是占位符）")
        except ImportError:
            st.caption(
                "⚠️ 还没装 `qrcode[pil]`——加进 requirements.txt 后 `pip install -r requirements.txt`，"
                "这里就会显示真的可扫二维码。现在先用下面的链接打开付款页。"
            )
        st.link_button("🔗 打开支付宝沙箱支付页", pay_url, width="stretch")
    else:
        st.markdown(
            f'<div style="margin:12px auto;width:120px;height:120px;border:1px dashed var(--primary-light);'
            f'border-radius:8px;display:flex;align-items:center;justify-content:center;'
            f'color:var(--text-secondary);font-size:0.7rem">[ 模拟二维码 ]<br>{order["order_id"][-8:]}</div>',
            unsafe_allow_html=True,
        )
        if ch == "alipay":
            st.caption(
                "⚠️ 这是纯模拟——没检测到 ALIPAY_APP_ID / ALIPAY_PRIVATE_KEY / ALIPAY_PUBLIC_KEY 三个环境变量。"
                "配好后（backend/payments.py 开头有申请沙箱凭证的步骤），这里会自动换成真实可扫的支付宝沙箱二维码。"
            )
        else:
            st.caption("⚠️ 微信支付目前还没接真实网关——backend/payments.py 现在只做了支付宝沙箱，这里先用模拟收银台占位。")

    if st.button("✅ 模拟支付成功", type="primary", width="stretch", key=f"confirm_{order['order_id']}"):
        _confirm(order)


def _render_card_channel(order):
    """银行卡：填卡号/有效期/CVV 的表单，不是扫码——银行卡收单本来就不是这个交互。
    后端目前也没接真实收单网关，这里填的信息只走一遍界面流程，不会被存储或真的扣款。
    """
    oid = order["order_id"]
    st.text_input("卡号", placeholder="6222 xxxx xxxx xxxx", key=f"card_no_{oid}")
    c1, c2 = st.columns(2)
    with c1:
        st.text_input("有效期 (MM/YY)", placeholder="08/29", key=f"card_exp_{oid}")
    with c2:
        st.text_input("CVV", placeholder="123", type="password", key=f"card_cvv_{oid}")
    st.caption("⚠️ 银行卡渠道后端还没接真实收单网关——这几个字段只是走一遍界面流程，不会被存储或真的扣款。")

    if st.button("✅ 模拟支付成功", type="primary", width="stretch", key=f"confirm_{oid}"):
        _confirm(order)


def _confirm(order):
    r = api_post(f"/api/boss/recharge/{order['order_id']}/confirm")
    if r.get("error"):
        st.error(r.get("detail", "支付失败"))
    else:
        st.success(f"充值成功 +{r['amount_credits']} credits")
        add_log(f"💰 充值到账 +{r['amount_credits']} credits（¥{r['amount_cny']}）", "success")
        st.session_state.recharge_order = None
        refresh_me()
        st.rerun()


with right:
    st.markdown("### 收银台")
    order = st.session_state.get("recharge_order")
    if not order:
        st.info("左侧选择渠道与金额后「生成支付」")
    else:
        _cashier_header(order)
        if order["channel"] == "card":
            _render_card_channel(order)
        else:
            _render_qr_channel(order)

st.divider()

# ── 充值订单流水 ──
st.markdown("### 充值流水")
orders = api_get("/api/boss/recharge/orders").get("orders", [])
if not orders:
    st.caption("暂无充值记录")
else:
    for o in orders:
        status = {"paid": "✅ 已到账", "pending": "⏳ 待支付", "failed": "❌ 失败"}.get(o["status"], o["status"])
        st.markdown(
            f'<div class="log-entry" style="flex-wrap:wrap">'
            f'<span class="log-time">{o["created_at"][:16]}</span>'
            f'<span>{CHANNEL_ICONS.get(o["channel"], "")} {o["channel_label"]}</span>'
            f'<b>¥{o["amount_cny"]:.2f}</b> → <b>{o["amount_credits"]:.0f} credits</b>'
            f'<span>{status}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )
