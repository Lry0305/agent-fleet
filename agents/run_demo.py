#!/usr/bin/env python3
"""
AgentFleet — Phase 3 演示：一人公司 agent 群（多任务并行 + 控制面）
====================================================================
老板（人，由本脚本模拟其在经营台的操作）往群里充钱 → 派活（DAG：
3 个 skill 并行 + 1 个串行汇总）→ 中途喊停/恢复 → 全部交付 → 自动结算。

    终端 1: ./run_all.sh        （启动链 + 后端 + 前端）
    终端 2: python agents/run_demo.py

全部走 HTTP API（agentfleet_sdk），不直接碰链。老板是人，不是 agent。
"""

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目根，供 `python agents/run_demo.py` 直接跑

from agents.agentfleet_sdk import AgentFleetClient

BASE = "http://127.0.0.1:8765"
PRICE = 5.0        # 每个 task 的价格 (credits)
WORK_DELAY = 1.0   # 每个 skill 步骤的模拟耗时 (秒)

# 演示老板账号（与 backend/config.py 的 DEMO_BOSS_* 一致，后端启动时自动播种）
DEMO_BOSS_PHONE = "13800000000"
DEMO_BOSS_PASSWORD = "demo123456"

GOAL = "写一份《AI Agent 群基础设施》调研简报"


# ── stub skill（真实分步 + 模拟延迟；skill 本身是用户自有能力，不属于基础设施）──

def skill_research(inp):
    yield "检索 agent 通信 / 编排相关资料…"
    yield "筛选 3 条关键来源…"
    yield "提取核心结论…"

def skill_compute(inp):
    yield "拉取协议动词集做规模测算…"
    yield "计算并行/串行的时延上限…"

def skill_write(inp):
    yield "起草大纲…"
    yield "写正文初稿…"
    yield "润色收尾…"

def skill_summary(inp):
    yield "汇总三份产物…"
    yield "生成最终简报…"

SKILLS = {
    "research": skill_research,
    "compute": skill_compute,
    "write": skill_write,
    "summary": skill_summary,
}


def run_task(c: AgentFleetClient, m: dict, name: str, skill: str):
    """provider 收到一条 spawn → 跑 skill（协作式 checkpoint）→ 交付 + 结算。"""
    p = m["payload"]
    task_id, tx_id, job_id = p["task_id"], p["tx_id"], p["job_id"]
    boss_did = m["from"]
    inp = p.get("input", {})
    steps = list(SKILLS[skill](inp))
    state = {
        "status": "running", "budget": p.get("price", 0),
        "task_id": task_id, "job_id": job_id, "skill": skill,
        "step": 0, "steps_total": len(steps), "partial_output": "",
    }

    print(f"\n▶ [{name}] 收到 spawn  skill={skill}  task={task_id}")
    c.send_status(task_id, "running", boss_did, job_id)
    c.save_checkpoint(task_id, state)

    log = lambda s: print(f"    [{name}] {s}")  # noqa: E731
    for i, step in enumerate(steps, start=1):
        print(f"    [{name}] {step}")
        time.sleep(WORK_DELAY)
        state["step"] = i
        state["partial_output"] = step
        c.save_checkpoint(task_id, state)  # 进度落盘（重启可续跑 / 老板可查）
        s = c.checkpoint(task_id, boss_did, job_id, state, log=log)
        if s == "cancelled":
            print(f"    [{name}] ⛔ 收到 cancel，让出（任务已由老板取消）")
            return

    # 完成：交付产物（内容寻址）→ 通知老板 → 自动结算
    output = {"skill": skill, "result": f"{name} 完成 {skill}", "input": inp}
    cid = ""
    try:
        delivered = c.deliver(tx_id, json.dumps(output, ensure_ascii=False))
        cid = delivered.get("cid", "")
    except Exception as e:
        print(f"    [{name}] ⚠️ deliver 失败: {e}")
    c.send_message(boss_did, "result", {"task_id": task_id, "cid": cid, "output": output}, thread_id=job_id)
    c.mark_task_done(task_id, cid)
    state.update({"status": "done", "output_cid": cid})
    c.save_checkpoint(task_id, state)  # 终态存续
    print(f"    [{name}] ✅ 交付 + 已结算  (cid={cid[:14]}…, workspace={c.workspace_dir})")


def worker_loop(c: AgentFleetClient, name: str, skill: str, stop: threading.Event):
    """后台轮询收件箱：见到未读 spawn → 消费 → 执行。"""
    processed = set()
    while not stop.is_set():
        try:
            inbox = c.receive(unread_only=True, limit=20)
            for m in inbox.get("messages", []):
                if m.get("type") == "spawn" and m["id"] not in processed:
                    processed.add(m["id"])
                    c.mark_read(m["id"])
                    run_task(c, m, name, skill)
        except Exception as e:
            print(f"  [{name}] 收件箱错误: {e}")
        time.sleep(0.5)


def main():
    print("=" * 64)
    print("  🏢 AgentFleet — 一人公司 agent 群（Phase 3）")
    print("=" * 64)

    # ── 老板（人）登录 + 创建 4 个名下 provider agent ──
    boss = AgentFleetClient(BASE)
    r = boss.login_boss(DEMO_BOSS_PHONE, DEMO_BOSS_PASSWORD)
    if r.get("error"):
        boss.register_boss("演示老板", DEMO_BOSS_PHONE, DEMO_BOSS_PASSWORD)
        boss.login_boss(DEMO_BOSS_PHONE, DEMO_BOSS_PASSWORD)
    order = boss.recharge("alipay", 10)          # 充值 ¥10 → +100 credits（mock 支付宝）
    boss.confirm_recharge(order["order_id"])
    print(f"\n👔 老板上线：充值 ¥10 → +100 credits，余额 {boss.get_balance()['credit_balance']}")

    roster = [("研究员", "research"), ("计算员", "compute"), ("写手", "write"), ("汇总员", "summary")]
    providers = []
    for name, skill in roster:
        c = AgentFleetClient(BASE)
        created = boss.create_agent(name, "provider", service_name=skill, price_per_call=PRICE)
        c.login(api_key=created["api_key"])       # worker 用返回的 api_key 登录
        c.private_key = created.get("private_key", "")  # 消息签名用（worker 运行时持有）
        providers.append({"name": name, "skill": skill, "client": c})
        print(f"🤖 {name} 上线（skill={skill}，要价 {PRICE} credits/次，归老板名下）")

    # ── provider 各自后台运行 ──
    stop = threading.Event()
    for p in providers:
        threading.Thread(target=worker_loop, args=(p["client"], p["name"], p["skill"], stop), daemon=True).start()
    time.sleep(1)  # 等 worker 就绪

    # ── 老板派活：3 并行 + 1 串行（summary 依赖前三个）──
    by_name = {p["name"]: p["client"]._agent.agent_id for p in providers}
    tasks = [
        {"ref": "r", "provider_id": by_name["研究员"], "skill": "research", "input": {"topic": "agent 通信"}, "price": PRICE},
        {"ref": "c", "provider_id": by_name["计算员"], "skill": "compute",   "input": {"topic": "规模测算"}, "price": PRICE},
        {"ref": "w", "provider_id": by_name["写手"],   "skill": "write",     "input": {"topic": "正文"},     "price": PRICE},
        {"ref": "s", "provider_id": by_name["汇总员"], "skill": "summary",   "input": {"sources": ["r", "c", "w"]},
         "price": PRICE, "depends_on": ["r", "c", "w"]},
    ]
    print(f"\n📋 老板派活：「{GOAL}」")
    print("   DAG：research ∥ compute ∥ write  →  summary（串行汇总）")
    job = boss.spawn_job(GOAL, tasks, budget=PRICE * 4)
    job_id = job["job_id"]
    print(f"   job_id={job_id}")

    # ── 中途喊停其中一支（整 job 暂停 → 对应 agent 让出）→ 恢复 ──
    time.sleep(2.5)
    print("\n⏸ 老板喊停：暂停任务……")
    boss.pause_job(job_id)
    time.sleep(2.0)
    print("▶ 老板恢复：继续任务……")
    boss.resume_job(job_id)

    # ── 等完成 ──
    while True:
        j = boss.get_job(job_id)
        if j["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(1)
    stop.set()

    # ── 结果 ──
    print("\n" + "=" * 64)
    print("  📊 结果")
    print("=" * 64)
    j = boss.get_job(job_id)
    print(f"  job 状态: {j['status']}  进度 {j['progress']['done']}/{j['progress']['total']}")
    print(f"  老板余额: {boss.get_balance()['credit_balance']} credits（含初始额度）")
    for p in providers:
        bal = p["client"].get_balance()
        print(f"  {p['name']}: 收入 {bal['total_earned'] - 100:.0f} credits（余额 {bal['credit_balance']}）")
    kinds = {e["kind"] for e in boss.get_events(limit=100).get("events", [])}
    print(f"  事件流种类: {sorted(kinds)}")
    print("\n✅ 全闭环演示完成：充钱 → 派活 → 并行/串行 → 喊停/恢复 → 交付 → 自动结算")


if __name__ == "__main__":
    main()
