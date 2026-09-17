"""
AgentFleet FastAPI 后端入口
=========================
启动: uvicorn backend.main:app --host 0.0.0.0 --port 8765 --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.routes import agents, services, billing, openclaw, feishu, reputation
from backend.routes import messages, artifacts, events, jobs, boss, skills

app = FastAPI(
    title="AgentFleet API",
    description="智能体群基础设施 — 身份 / 通信 / 交付 / 结算 / 编排 / 控制 / 事件流（Phase 3）",
    version="4.0.0",
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
app.include_router(messages.router)
app.include_router(artifacts.router)
app.include_router(events.router)
app.include_router(jobs.router)
app.include_router(boss.router)
app.include_router(skills.router)


@app.on_event("startup")
def startup():
    init_db()
    boss.ensure_demo_boss()


@app.get("/")
def root():
    return {
        "name": "AgentFleet API",
        "version": "4.0.0",
        "docs": "/docs",
        "endpoints": {
            "agents": "/api/agents",
            "services": "/api/services",
            "billing": "/api/billing",
            "openclaw": "/api/openclaw",
            "feishu": "/api/feishu",
            "reputation": "/api/reputation",
            "messages": "/api/messages",
            "jobs": "/api/jobs",
            "events": "/api/events",
            "boss": "/api/boss",
            "skills": "/api/skills",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}
