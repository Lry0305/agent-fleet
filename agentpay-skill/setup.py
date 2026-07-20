"""AgentPay Skill — 独立安装包"""
from setuptools import setup, find_packages

setup(
    name="agentpay-skill",
    version="1.0.0",
    description="AgentPay Provider Skill: CUA-driver macOS automation + on-chain settlement",
    packages=find_packages(),
    install_requires=[
        "cua-driver>=0.1.0",
        "web3>=6.0.0",
        "click>=8.0.0",
        "rich>=13.0.0",
        "requests>=2.28.0",
    ],
    entry_points={
        "console_scripts": [
            "agentpay-skill = agentpay_skill.cli:main",
        ],
    },
    python_requires=">=3.10",
)
