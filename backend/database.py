"""
SQLite 数据库连接 + 初始化
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖：每次请求一个数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """创建所有表（首次启动时调用）"""
    import backend.models  # noqa: 确保模型被注册
    Base.metadata.create_all(bind=engine)
    _migrate(engine)


def _migrate(engine):
    """轻量迁移：给老表补缺失列（demo 场景，无 Alembic）。幂等。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        cols = {c["name"] for c in insp.get_columns("messages")}
        if "protocol_version" not in cols:
            conn.execute(text(
                "ALTER TABLE messages ADD COLUMN protocol_version VARCHAR(16) DEFAULT '1.0'"
            ))

        # 老板控制台：agents 表补 boss_id / phone / password_hash（幂等）
        agent_cols = {c["name"] for c in insp.get_columns("agents")}
        if "boss_id" not in agent_cols:
            conn.execute(text("ALTER TABLE agents ADD COLUMN boss_id VARCHAR(64)"))
        if "phone" not in agent_cols:
            conn.execute(text("ALTER TABLE agents ADD COLUMN phone VARCHAR(32)"))
        if "password_hash" not in agent_cols:
            conn.execute(text("ALTER TABLE agents ADD COLUMN password_hash VARCHAR(256)"))
        if "workspace_mode" not in agent_cols:
            conn.execute(text("ALTER TABLE agents ADD COLUMN workspace_mode VARCHAR(32) DEFAULT 'isolated'"))
        if "avatar_color" not in agent_cols:
            conn.execute(text("ALTER TABLE agents ADD COLUMN avatar_color VARCHAR(16) DEFAULT '#3370ff'"))

        # Phase 4 验收流程 + 竞价定价：tasks 表补 require_review / output_json / price_tier
        task_cols = {c["name"] for c in insp.get_columns("tasks")}
        if "require_review" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN require_review BOOLEAN DEFAULT 0"))
        if "output_json" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN output_json TEXT DEFAULT '{}'"))
        if "price_tier" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN price_tier VARCHAR(32) DEFAULT ''"))

        # 按 token 结算：tasks 表补 token_count / settled_price
        if "token_count" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN token_count INTEGER DEFAULT 0"))
        if "settled_price" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN settled_price FLOAT DEFAULT 0.0"))

        # 支付宝沙箱：recharge_orders 表补 is_sandbox_real
        recharge_cols = {c["name"] for c in insp.get_columns("recharge_orders")}
        if "is_sandbox_real" not in recharge_cols:
            conn.execute(text("ALTER TABLE recharge_orders ADD COLUMN is_sandbox_real BOOLEAN DEFAULT 0"))

        # 技能包：skills 表补 description / local_path（都是晚于 content 加的字段）——
        # 只有表已存在时才需要补列，全新建库走 create_all 会直接带上，这里只是保险
        if "skills" in insp.get_table_names():
            skill_cols = {c["name"] for c in insp.get_columns("skills")}
            if "description" not in skill_cols:
                conn.execute(text("ALTER TABLE skills ADD COLUMN description TEXT DEFAULT ''"))
            if "local_path" not in skill_cols:
                conn.execute(text("ALTER TABLE skills ADD COLUMN local_path TEXT DEFAULT ''"))
