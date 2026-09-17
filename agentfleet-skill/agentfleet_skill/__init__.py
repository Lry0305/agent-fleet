"""
AgentFleet Skill — 独立子项目
============================
Provider Agent 完整方案：CUA-driver 桌面自动化 + AgentFleet SDK 支付 + 链上结算

用法:
    # 命令行
    agentfleet-skill onboard --name StockMaster --auth feishu_app ...
    agentfleet-skill serve
    agentfleet-skill pay --provider ag_xxx --amount 10

    # Python API
    from agentfleet_skill import AgentFleetSkill
    skill = AgentFleetSkill()
    skill.onboard(...)
    skill.serve()
"""

from .cli import main as cli_main
