"""
AgentPay Skill — 独立子项目
============================
Provider Agent 完整方案：CUA-driver 桌面自动化 + AgentPay SDK 支付 + 链上结算

用法:
    # 命令行
    agentpay-skill onboard --name StockMaster --auth feishu_app ...
    agentpay-skill serve
    agentpay-skill pay --provider ag_xxx --amount 10

    # Python API
    from agentpay_skill import AgentPaySkill
    skill = AgentPaySkill()
    skill.onboard(...)
    skill.serve()
"""

from .cli import main as cli_main
