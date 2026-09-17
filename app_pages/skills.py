"""
我的技能页：三层模型都在这——服务名称（身份/派活标签）、技能包（可装配的方法论文本）、
MCP 连接器（外部连接，还没做，见页面底部）。

技能包＝一段方法论/知识文本，跟服务名称是两回事：service_name 决定"派活时选不选得到你"，
技能包决定"选到你之后，你被交代了哪些方法论"——worker 轮询 GET /api/tasks 拿任务时，
provider 当前装配的技能包内容会随任务 payload 一起吐给它，由 worker 自己塞进调用 LLM
时的 prompt。纯文本，不挑模型，不是 Claude 专属，OpenClaw 机器人一样能读。

范围克制：技能包库只是老板自己维护的私有库（建/装/卸），不接 GitHub 技能市场、不做一键安装——
目前没有第三方发布跟这套协议兼容的 skill 仓库，广场没有真实数据源可抓；一键安装本质是帮你在
电脑上跑第三方代码，有任意代码执行的安全风险，不该是点个按钮就悄悄装好的事，等以后真有外部
生态了再考虑。
"""

import streamlit as st

from ui.api import api_get, api_post, api_delete
from ui.session import add_log
from ui.components import avatar_badge

st.markdown('<p class="app-title">我的技能</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="app-subtitle" style="color:var(--text-secondary);font-size:0.85rem">'
    "服务名称（身份标签）· 技能包（可装配的方法论）· MCP 连接器（还没做）</p>",
    unsafe_allow_html=True,
)

if not st.session_state.logged_in:
    st.warning("⚠️ 请先在左侧边栏登录老板账号")
    st.stop()

agents = api_get("/api/boss/agents").get("agents", [])
providers = [a for a in agents if a.get("role") == "provider"]


# ── 第一层：服务名称（身份/派活标签）──

def _is_listed(a: dict) -> bool:
    """价格全平台统一，不用再看 price_per_call 有没有挂牌——只要声明了 service_name 就算挂牌。"""
    return bool(a.get("service_name"))


listed = [a for a in providers if _is_listed(a)]
unlisted = [a for a in providers if not _is_listed(a)]

st.markdown("### 🏷 服务名称")
st.caption(f"👥 {len(providers)} 个执行 Agent · 🏷 {len(listed)} 个已声明服务名称")

if not providers:
    st.info("还没有任何执行 Agent——去「我的 Agent」创建一个。")
else:
    if listed:
        for a in listed:
            active = a.get("is_service_active", True)
            with st.container(border=True):
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
                    f'<span>{avatar_badge(a.get("avatar","🤖"), a.get("avatar_color"))} '
                    f'<b>{a.get("name","?")}</b> · <code>{a.get("service_name")}</code></span>'
                    f'<span style="font-size:0.85rem">{("🟢 在线" if active else "⚪ 停用")}</span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                desc = a.get("service_description") or "—"
                st.markdown(
                    f'<div class="agent-meta">{desc} · 统一费率 10.0 credits/1k tokens</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.caption("还没有 agent 声明服务名称。")

    if unlisted:
        with st.expander(f"还没声明服务名称的 Agent（{len(unlisted)} 个）", expanded=False):
            for a in unlisted:
                st.markdown(
                    f'<div class="log-entry" style="flex-wrap:wrap">'
                    f'{avatar_badge(a.get("avatar","🤖"), a.get("avatar_color"))} '
                    f'<b>{a.get("name","?")}</b>'
                    f'<span style="color:#94a3b8;font-size:0.75rem">'
                    f'还没声明 service_name——去「我的 Agent」页编辑，或 agent 自己用 api_key '
                    f'调 PUT /api/agents/me 设置</span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

st.divider()

# ── 第二层：技能包库（可装配的方法论文本，独立于服务名称）──

st.markdown("### 🧩 技能包库")

with st.form("create_skill", clear_on_submit=True):
    skc1, skc2 = st.columns([1, 2])
    with skc1:
        skill_name = st.text_input("技能包名称", placeholder="例: 财报速读方法论")
    with skc2:
        skill_desc = st.text_input(
            "说明书（适用场景，一两句话）",
            placeholder="例: 拿到一份财报之后，判断这家公司基本面好不好用",
        )
    skill_content = st.text_area(
        "正文（具体怎么做，例子/步骤都写在这里面——local_path 留空时才会用到这段）",
        placeholder="例:\n1. 先看经营性现金流是不是正的，比净利润更难做假\n"
                    "2. 再看毛利率环比/同比变化，掉了要么是价格战要么是成本上升\n"
                    "3. 最后看管理层措辞——用词从「稳健增长」变成「充满挑战」通常是预警\n\n"
                    "示例：某公司 Q3 现金流转负但净利润仍为正 → 优先怀疑收入确认方式，标记为高风险",
        height=160,
    )
    skill_path = st.text_input(
        "本地路径（可选——你自己电脑上已经装好的技能文件夹，比如 Claude Code 认的 SKILL.md 目录）",
        placeholder="例: /Users/你的用户名/skills/财报速读方法论",
        help="填了这个，worker（agentfleet-skill serve）会在 --exec 命令后追加 --add-dir 这个路径，"
             "交给 claude/codex 这类工具自己去读那个目录——不是把内容抄进正文，是真的给目录访问权限。"
             "只在 worker 进程和这个路径在同一台机器上时才有用。",
    )
    if st.form_submit_button("➕ 创建技能包", type="primary"):
        if not skill_name.strip():
            st.error("先起个名字")
        else:
            r = api_post("/api/skills", {
                "name": skill_name, "description": skill_desc,
                "content": skill_content, "local_path": skill_path,
            })
            if r.get("error"):
                st.error(r.get("detail", "创建失败"))
            else:
                add_log(f"🧩 已创建技能包：{skill_name}", "success")
                st.rerun()

all_skills = api_get("/api/skills").get("skills", [])
if not all_skills:
    st.caption("还没有技能包——上面创建第一个。")
else:
    for s in all_skills:
        skr1, skr2 = st.columns([6, 1])
        with skr1:
            path_badge = " · 📁 挂本地目录" if s.get("local_path") else ""
            st.markdown(f"**{s['name']}**{path_badge}")
            st.caption(s.get("description") or "（还没写说明书——建议补一句适用场景）")
            if s.get("local_path"):
                st.caption(f"📁 `{s['local_path']}`")
            with st.expander("查看正文", expanded=False):
                st.text(s.get("content") or "（正文为空——这个技能包靠本地路径挂载，不需要正文）")
        with skr2:
            if st.button("🗑", key=f"del_skill_{s['skill_id']}"):
                r = api_delete(f"/api/skills/{s['skill_id']}")
                if not r.get("error"):
                    add_log(f"🗑 已删除技能包：{s['name']}", "warning")
                    st.rerun()

st.divider()

# ── 技能包装配：给每个 provider agent 选它该装哪些技能包 ──

st.markdown("### 🧷 技能包装配")

if not providers:
    st.caption("还没有执行 Agent——去「我的 Agent」创建一个再回来装配。")
elif not all_skills:
    st.caption("技能库还是空的——先在上面创建一个技能包。")
else:
    name_by_id = {s["skill_id"]: s["name"] for s in all_skills}
    for a in providers:
        aid = a["agent_id"]
        with st.container(border=True):
            st.markdown(
                f'{avatar_badge(a.get("avatar","🤖"), a.get("avatar_color"))} '
                f'**{a.get("name","?")}**' + (f' · <code>{a.get("service_name")}</code>' if a.get("service_name") else ""),
                unsafe_allow_html=True,
            )
            installed = api_get(f"/api/skills/agents/{aid}").get("skills", [])
            installed_ids = {s["skill_id"] for s in installed}
            selected_ids = st.multiselect(
                "已装配技能包", options=[s["skill_id"] for s in all_skills],
                default=[sid for sid in installed_ids if sid in name_by_id],
                format_func=lambda sid: name_by_id.get(sid, sid),
                key=f"skills_ms_{aid}",
                label_visibility="collapsed",
            )
            if st.button("应用", key=f"apply_skills_{aid}"):
                to_install = set(selected_ids) - installed_ids
                to_uninstall = installed_ids - set(selected_ids)
                for sid in to_install:
                    api_post(f"/api/skills/agents/{aid}/install", {"skill_id": sid})
                for sid in to_uninstall:
                    api_delete(f"/api/skills/agents/{aid}/install/{sid}")
                if to_install or to_uninstall:
                    add_log(f"🧩 已更新「{a.get('name')}」的技能装配", "success")
                    st.rerun()
                else:
                    st.info("没有变化")

st.divider()
st.caption(
    "🚧 第三层 MCP 连接器（飞书 / OpenClaw 外部连接）还没做，跟服务名称、技能包都是独立的一件事。"
)
st.caption(
    "🚧 GitHub 热门 skill 广场 + 一键安装也还没做——目前没有第三方发布跟这套协议兼容的仓库，"
    "没有真实数据源可抓；一键安装本质是帮你在电脑上跑第三方代码，有安全风险，不该是点个按钮就悄悄装好。"
)
