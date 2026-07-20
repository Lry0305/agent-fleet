"""
AgentPay FastAPI 后端入口
=========================
启动: uvicorn backend.main:app --host 0.0.0.0 --port 8765 --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.routes import agents, services, billing, openclaw, feishu, reputation

app = FastAPI(
    title="AgentPay API",
    description="AI Agent 去中心化支付平台 — OpenClaw / 飞书 / CUA-driver 集成",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(agents.router)
app.include_router(services.router)
app.include_router(billing.router)
app.include_router(openclaw.router)
app.include_router(feishu.router)
app.include_router(reputation.router)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def root():
    return {
        "name": "AgentPay API",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "agents": "/api/agents",
            "services": "/api/services",
            "billing": "/api/billing",
            "openclaw": "/api/openclaw",
            "feishu": "/api/feishu",
            "reputation": "/api/reputation",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}
