"""
多认证方式中间件
================
支持三种认证:
  - Bearer ap_sk_xxx    → API Key 验证
  - Bearer feishu:xxx   → 飞书 App ID + Secret 验证
  - Bearer openclaw:xxx → OpenClaw Bot Token 验证
"""

from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import AgentModel, AgentRole, AuthMethod

security = HTTPBearer(auto_error=False)


def verify_agent(
    credentials: HTTPAuthorizationCredentials | None = Security(security),
    db: Session = Depends(get_db),
) -> AgentModel:
    """
    统一认证入口：根据 token 前缀自动选择验证方式。
    
    Token 格式:
      ap_sk_xxx        → api_key 验证
      feishu:app_id:secret → feishu_app 验证
      openclaw:token   → openclaw_bot 验证
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="缺少认证信息")

    token = credentials.credentials

    # ── 方式1: API Key ──
    if token.startswith("ap_sk_"):
        key_hash = AgentModel.hash_secret(token)
        agent = db.query(AgentModel).filter(
            AgentModel.secret_hash == key_hash
        ).first()
        if agent:
            return agent
        raise HTTPException(status_code=401, detail="API Key 无效")

    # ── 方式2: 飞书 App ID ──
    if token.startswith("feishu:"):
        parts = token.split(":", 2)
        if len(parts) != 3:
            raise HTTPException(status_code=401, detail="飞书认证格式: feishu:app_id:app_secret")
        app_id = parts[1]
        app_secret = parts[2]
        secret_hash = AgentModel.hash_secret(app_secret)
        agent = db.query(AgentModel).filter(
            AgentModel.auth_method == AuthMethod.FEISHU_APP,
            AgentModel.app_id == app_id,
            AgentModel.app_secret_hash == secret_hash,
        ).first()
        if agent:
            return agent
        raise HTTPException(status_code=401, detail="飞书 App ID 或 Secret 无效")

    # ── 方式3: OpenClaw Bot Token ──
    if token.startswith("openclaw:"):
        bot_token = token.split(":", 1)[1]
        secret_hash = AgentModel.hash_secret(bot_token)
        agent = db.query(AgentModel).filter(
            AgentModel.auth_method == AuthMethod.OPENCLAW_BOT,
            AgentModel.app_secret_hash == secret_hash,
        ).first()
        if agent:
            return agent
        raise HTTPException(status_code=401, detail="OpenClaw Bot Token 无效")

    raise HTTPException(status_code=401, detail="不支持的认证方式")


# 兼容旧代码的别名
verify_api_key = verify_agent


def verify_boss(
    credentials: HTTPAuthorizationCredentials | None = Security(security),
    db: Session = Depends(get_db),
) -> AgentModel:
    """老板专属依赖：先 verify_agent 再校验 role==boss。"""
    agent = verify_agent(credentials, db)
    if agent.role != AgentRole.BOSS:
        raise HTTPException(status_code=403, detail="只有老板可以执行此操作")
    return agent


def verify_webhook_signature(signature: str, body: str, secret: str) -> bool:
    import hmac
    expected = hmac.new(secret.encode(), body.encode(), "sha256").hexdigest()
    return hmac.compare_digest(expected, signature)
