"""
我的 Agent 页：老板创建 / 管理名下的 agent（改名 / 开关 / 销毁）。价格全平台统一，老板/agent 都改不了。
agent 归老板所有；api_key 是 worker 运行时的内部凭证，创建时一次性展示。
"""

import streamlit as st

from ui.api import api_get, api_post, api_put, api_delete
from ui.session import add_log
from ui.components import role_tag, avatar_badge, AVATAR_PRESETS

st.markdown('<p class="app-title">我的 Agent</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="app-subtitle" style="color:var(--text-secondary);font-size:0.85rem">'
    "老板创建并管理名下的 agent 群 · 支持改名 / 开关 / 销毁</p>",
    unsafe_allow_html=True,
)

if not st.session_state.logged_in:
    st.warning("⚠️ 请先在左侧边栏登录老板账号")
    st.stop()


AUTH_METHOD_LABELS = {
    "api_key": "API Key（默认，配合 agentfleet-skill 或自己的脚本）",
    "feishu_app": "飞书 App（真实 App ID + App Secret）",
    "openclaw_bot": "OpenClaw Bot（真实 Bot Token）",
}


def _create_form():
    with st.expander("➕ 创建 Agent", expanded=False):
        st.caption("一人公司里只有你在派活，这里创建的都是执行 Agent；"
                    "价格全平台统一（不用它自己报价，也不用你猜），起始余额 0——"
                    "填好服务名称它就能被派活，选它还是选别的 agent 看任务适不适配，跟价格无关。"
                    "真正让它「会干活」的方法论文本，去「我的技能」页里建、再装配到它身上。")
        with st.form("create_agent"):
            c1, c2 = st.columns(2)
            with c1:
                name = st.text_input("Agent 名称", placeholder="例: 研究员")
                auth_method = st.selectbox(
                    "认证方式", ["api_key", "feishu_app", "openclaw_bot"],
                    format_func=lambda x: AUTH_METHOD_LABELS[x],
                )
            with c2:
                avatar_idx = st.selectbox(
                    "头像", range(len(AVATAR_PRESETS)),
                    format_func=lambda i: AVATAR_PRESETS[i][0],
                )
                avatar, avatar_color, _ = AVATAR_PRESETS[avatar_idx]
                service_name = st.text_input("服务名称", placeholder="例: research")
                service_desc = st.text_input("服务描述", placeholder="例: 检索并提炼资料")

            st.caption("下面两项只有选「飞书 App」/「OpenClaw Bot」才需要填——填的是真实凭证，"
                       "不是随便起的名字；OpenClaw 只需要 Bot Token，填进「App ID / Bot Token」就行。")
            cc1, cc2 = st.columns(2)
            with cc1:
                app_id = st.text_input("App ID / Bot Token", placeholder="cli_xxx 或 Bot Token")
            with cc2:
                app_secret = st.text_input("App Secret（飞书需要，OpenClaw 可留空）", type="password")

            workspace_mode = st.selectbox(
                "工作空间", ["isolated", "shared_pool"],
                format_func=lambda x: {
                    "isolated": "🔒 独立空间（默认，私有 memory，平台不没收）",
                    "shared_pool": "🗂 共享知识池（额外挂载本老板名下的共享参考目录）",
                }[x],
            )
            # 「简介」跟「服务描述」是重复字段——而且哪儿都没展示过（manifest/agent 卡片用的
            # 都是 service_description），所以这里不再单独收集，避免填两遍一样的话。

            fc1, fc2 = st.columns([1, 2])
            with fc1:
                test_clicked = st.form_submit_button("🔌 测试连接", width="stretch")
            with fc2:
                create_clicked = st.form_submit_button("创建", type="primary", width="stretch")

            if test_clicked:
                # 表单里两个提交按钮共享同一次提交——这里只测连接，不建 agent。
                # 真打一次飞书开放平台的 token 接口，App ID/Secret 配不对飞书会直接拒绝，
                # 不是"填了就当真"；OpenClaw 目前没有已知的公开校验接口，老实说做不了。
                if auth_method == "feishu_app":
                    if not app_id or not app_secret:
                        st.error("先把 App ID 和 App Secret 都填上再测")
                    else:
                        tr = api_post("/api/boss/agents/test-feishu-app", {
                            "app_id": app_id, "app_secret": app_secret,
                        })
                        if tr.get("error"):
                            st.error(tr.get("detail", "测试失败"))
                        elif tr.get("ok"):
                            st.success(tr.get("message"))
                        else:
                            st.error(tr.get("message"))
                elif auth_method == "openclaw_bot":
                    st.warning("OpenClaw 目前没有已知的公开校验接口，这里没法帮你真测——"
                               "直接创建后跑一次真实 worker 连接最准。")
                else:
                    st.info("API Key 认证不需要测连接，直接点「创建」就行。")

            if create_clicked:
                r = api_post("/api/boss/agents", {
                    "name": name, "role": "provider", "service_name": service_name,
                    "service_description": service_desc,
                    "avatar": avatar, "avatar_color": avatar_color,
                    "workspace_mode": workspace_mode,
                    "auth_method": auth_method, "app_id": app_id, "app_secret": app_secret,
                })
                if r.get("error"):
                    st.error(r.get("detail", "创建失败"))
                else:
                    api_key = r.get("api_key", "")
                    st.session_state.new_agent_key = {
                        "name": r["agent"]["name"], "api_key": api_key, "auth_method": auth_method,
                    }
                    add_log(f"🤖 已创建执行 Agent：{name}", "success")
                    st.rerun()


def _import_form():
    with st.expander("📥 导入已有 Agent", expanded=False):
        st.caption("适用于你在别处已经部署、已经有自己报价的 agent："
                    "同名或同服务地址会更新已有登记，不会重复建号。")
        with st.form("import_agent"):
            c1, c2 = st.columns(2)
            with c1:
                iname = st.text_input("Agent 名称", placeholder="例: 外部研究员", key="imp_name")
                iservice_name = st.text_input("服务名称", placeholder="例: research", key="imp_svc")
                iprice = st.number_input("已声明报价 (credits/次)", 0.01, 1000.0, 5.0, key="imp_price")
            with c2:
                iendpoint = st.text_input("服务地址（可选）", placeholder="https://...", key="imp_endpoint")
                iavatar = st.text_input("头像", "🤖", key="imp_avatar")
                iworkspace_mode = st.selectbox(
                    "工作空间", ["isolated", "shared_pool"],
                    format_func=lambda x: {"isolated": "🔒 独立空间", "shared_pool": "🗂 共享知识池"}[x],
                    key="imp_ws",
                )
            iservice_desc = st.text_input("服务描述", placeholder="例: 检索并提炼资料", key="imp_desc")

            if st.form_submit_button("导入", type="primary", width="stretch"):
                r = api_post("/api/boss/agents/import", {"agents": [{
                    "name": iname, "service_name": iservice_name, "service_description": iservice_desc,
                    "service_endpoint": iendpoint, "price_per_call": iprice, "avatar": iavatar,
                    "description": "", "workspace_mode": iworkspace_mode,
                }]})
                if r.get("error"):
                    st.error(r.get("detail", "导入失败"))
                else:
                    added = (r.get("agents") or [{}])[0]
                    action_label = "更新" if added.get("action") == "updated" else "新建"
                    add_log(f"📥 已{action_label}导入 agent：{iname}", "success")
                    st.rerun()


def _agent_card(a: dict):
    aid = a["agent_id"]
    active = a.get("is_service_active", True)
    with st.container(border=True):
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
            f'<span>{avatar_badge(a.get("avatar","🤖"), a.get("avatar_color"))} '
            f'<b>{a.get("name","?")}</b></span>'
            f'<span style="font-size:0.85rem">{role_tag(a.get("role"))} · '
            f'{("🟢 在线" if active else "⚪ 停用")}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )
        svc = a.get("service_name") or "—"
        ws_mode = (a.get("workspace") or {}).get("mode", "isolated")
        ws_label = "🗂 共享池" if ws_mode == "shared_pool" else "🔒 独立空间"
        # provider 展示真实结算记录（已交付验收过几次、累计挣了多少），不是一个静态余额数字——
        # 那个数字之前会被创建时凭空塞的 100 credits、或老板手动划的启动资金混在一起，看着像
        # "业绩"，其实很大一部分跟它有没有干过活没关系。真实结算次数/金额直接查 TaskModel。
        if a.get("role") == "provider":
            settled_txt = f'已结算 {a.get("settled_count", 0)} 次 · 累计 {a.get("settled_total", 0):.1f} credits'
        else:
            settled_txt = f'余额 {a.get("credit_balance", 0):.1f} credits'
        st.markdown(
            f'<div class="agent-meta">{svc} · 统一费率 10.0 credits/1k tokens（全平台一致）· '
            f'{settled_txt} · {ws_label}</div>',
            unsafe_allow_html=True,
        )

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            label = "⏸ 停用" if active else "▶ 启用"
            if st.button(label, key=f"toggle_{aid}", width="stretch"):
                r = api_put(f"/api/boss/agents/{aid}", {"is_service_active": not active})
                if not r.get("error"):
                    add_log(f"{label} agent：{a.get('name')}", "info")
                    st.rerun()
        with c2:
            with st.popover("✏️ 编辑"):
                new_name = st.text_input("名称", a.get("name", ""), key=f"nm_{aid}")
                st.caption("价格全平台统一，这里改不了单价——只能改名字/描述，开关服务用外面的按钮")
                new_desc = st.text_input("服务描述", a.get("service_description", ""), key=f"sd_{aid}")
                if st.button("保存", key=f"save_{aid}", width="stretch"):
                    r = api_put(f"/api/boss/agents/{aid}", {
                        "name": new_name,
                        "service_description": new_desc,
                    })
                    if not r.get("error"):
                        add_log(f"✏️ 已更新 agent：{new_name}", "success")
                        st.rerun()
        with c3:
            with st.popover("🔑 重置"):
                st.caption("key 泄露了（截图、分享屏幕、聊天记录带出明文）就在这重置——旧 key 立即失效，"
                           "正在跑的 worker 进程要换成新 key 才能继续认证。")
                if st.button("确认重置", key=f"rot_{aid}", width="stretch"):
                    r = api_post(f"/api/boss/agents/{aid}/rotate-key", {})
                    if r.get("error"):
                        st.error(r.get("detail", "重置失败"))
                    else:
                        st.session_state.rotated_key = {
                            "agent_id": aid, "name": a.get("name"), "api_key": r["api_key"],
                        }
                        add_log(f"🔑 已重置 api_key：{a.get('name')}", "warning")
                        st.rerun()
        with c4:
            st.caption(f"ID {aid[:10]}…")
        with c5:
            with st.popover("🗑 销毁"):
                st.warning(f"确定销毁 {a.get('name')}？不可恢复。")
                if st.button("确认销毁", key=f"del_{aid}", width="stretch"):
                    r = api_delete(f"/api/boss/agents/{aid}")
                    if not r.get("error"):
                        add_log(f"🗑 已销毁 agent：{a.get('name')}", "warning")
                        st.rerun()

        if st.session_state.get("rotated_key", {}).get("agent_id") == aid:
            rk = st.session_state.rotated_key
            st.info(f"🔑 {rk['name']} 的新 api_key（仅此一次展示）：")
            st.text_input("rotated_key", rk["api_key"], type="password",
                           key=f"rotated_key_display_{aid}", label_visibility="collapsed")
            if st.button("知道了", key=f"dismiss_rot_{aid}"):
                st.session_state.rotated_key = None
                st.rerun()


_create_form()
_import_form()

# 一次性展示新建 agent 的 api_key——它是这个 agent 的 worker 进程用来调 API 的身份凭证
# （谁拿着这把 key 请求，平台就认成是这个 agent 本人），密码框打码展示，不直接摆明文。
if st.session_state.get("new_agent_key"):
    nk = st.session_state.new_agent_key
    st.info(f"🔑 {nk['name']} 的 api_key（仅此一次展示，用完记得复制走）：")
    st.text_input("api_key", nk["api_key"], type="password",
                   key="new_agent_key_display", label_visibility="collapsed")
    st.caption("泄露了就去下面对应 agent 卡片点「🔑 重置」换一把。")
    if st.button("知道了", key="dismiss_key"):
        st.session_state.new_agent_key = None
        st.rerun()

st.divider()
st.markdown("### 名下 Agent")

agents = api_get("/api/boss/agents").get("agents", [])
if not agents:
    st.info("还没有 agent —— 点上方「➕ 创建 Agent」建第一个。")
else:
    for a in agents:
        _agent_card(a)
