"""
飞书 API 客户端
==============
获取 tenant_access_token、发送消息、解析事件。
不需要 OpenClaw，直接调飞书开放平台 API。
"""

import json
import time
import requests
from typing import Optional

FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuClient:
    """
    飞书 API 封装。
    每个飞书机器人(应用)需要自己的 app_id + app_secret。
    """

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: str = ""
        self._token_expires: float = 0

    def _get_tenant_token(self) -> str:
        """获取 tenant_access_token（自动缓存到过期前）"""
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        r = requests.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书token获取失败: {data.get('msg', '')}")
        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200)
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_tenant_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def reply_text(self, message_id: str, text: str) -> dict:
        """
        回复一条飞书消息（回复到群聊/私聊）。
        Args:
            message_id: 收到的消息 ID（回复此条）
            text: 回复的文本内容
        Returns:
            飞书 API 响应
        """
        r = requests.post(
            f"{FEISHU_BASE}/im/v1/messages/{message_id}/reply",
            headers=self._headers(),
            json={
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "msg_type": "text",
            },
            timeout=10,
        )
        return r.json()

    def send_text(self, chat_id: str, text: str) -> dict:
        """
        主动发送消息到群聊/用户。
        Args:
            chat_id: 群聊 ID 或用户 open_id
            text: 文本内容
        """
        r = requests.post(
            f"{FEISHU_BASE}/im/v1/messages?receive_id_type=chat_id",
            headers=self._headers(),
            json={
                "receive_id": chat_id,
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "msg_type": "text",
            },
            timeout=10,
        )
        return r.json()

    @staticmethod
    def parse_text_content(content_str: str) -> str:
        """
        解析飞书消息 content（去掉 @机器人 前缀）。
        飞书 text 消息的 content 是 JSON 字符串:
            {"text":"@机器人 腾讯怎么样"}
        """
        try:
            content = json.loads(content_str)
            text = content.get("text", "")
            # 去掉 @机器人（格式: @_user_id 文本）
            import re
            text = re.sub(r'@_?\w+', '', text).strip()
            return text
        except (json.JSONDecodeError, KeyError):
            return content_str

    @staticmethod
    def verify_challenge(payload: dict) -> Optional[str]:
        """
        飞书 URL 验证挑战。
        返回 challenge 字符串，或 None（如果不是验证请求）。
        """
        if payload.get("type") == "url_verification":
            return payload.get("challenge")
        return None
