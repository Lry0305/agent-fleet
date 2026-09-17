"""
AgentFleet — 前端 API 客户端
===========================
薄封装 requests，统一携带 Authorization 头 + 错误归一化。
"""

import os
import requests
import streamlit as st

def _api_base() -> str:
    # 部署时后端地址走 AGENTFLEET_API：优先环境变量（Streamlit Cloud 会把顶级 secrets 注入为
    # 环境变量），再兜底读 st.secrets（本地 .streamlit/secrets.toml）。st.secrets 在没有任何
    # secrets 文件时会抛 StreamlitSecretNotFoundError，所以必须兜住，不能裸调。
    base = os.getenv("AGENTFLEET_API")
    if base:
        return base
    try:
        return st.secrets.get("AGENTFLEET_API", "http://127.0.0.1:8765")
    except Exception:
        return "http://127.0.0.1:8765"


API_BASE = _api_base()


def api_h():
    h = {"Content-Type": "application/json"}
    if st.session_state.get("api_key"):
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


def api_put(path, data=None):
    try:
        r = requests.put(f"{API_BASE}{path}", headers=api_h(), json=data or {}, timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}
    except Exception as e:
        return {"error": True, "detail": str(e)}


def api_delete(path):
    try:
        r = requests.delete(f"{API_BASE}{path}", headers=api_h(), timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}
    except Exception as e:
        return {"error": True, "detail": str(e)}


def api_put_as(api_key, path, data=None):
    """用指定 agent 自己的 api_key 发起 PUT（而不是当前登录老板的）。

    价格这类「agent 自主声明」的字段必须以 agent 自己的身份写入，不能借老板的权限代填——
    这里只是把这次 HTTP 调用换成用新建 agent 自己刚拿到的 api_key 发起，语义上仍然是
    「它自己声明了报价」，老板只是在创建时顺手替它把第一次声明捎带做了。
    """
    try:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        r = requests.put(f"{API_BASE}{path}", headers=headers, json=data or {}, timeout=10)
        return r.json() if r.ok else {"error": True, "detail": r.text}
    except Exception as e:
        return {"error": True, "detail": str(e)}
