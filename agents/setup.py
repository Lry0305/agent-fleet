#!/usr/bin/env python3
"""
部署 + 配置自动化
================
运行 Hardhat deploy 脚本 —— 部署后地址由 deploy.js 自动写入项目根 chain_addresses.json，
backend/config.py 与 agents/config.py 启动时自动读取，无需再手动改 config.py。
"""

import subprocess
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def deploy_contracts():
    """运行 npx hardhat run scripts/deploy.js --network localhost"""
    print("🔧 部署合约到本地链...")
    result = subprocess.run(
        ["npx", "hardhat", "run", "scripts/deploy.js", "--network", "localhost"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    print(output)

    if result.returncode != 0:
        print("❌ 部署失败！请确保 Hardhat 节点已启动 (npx hardhat node)")
        sys.exit(1)

    return output


def main():
    print("=" * 50)
    print("  AgentFleet — 合约部署")
    print("=" * 50)

    deploy_contracts()
    print("\n✅ 部署完成，合约地址已写入 chain_addresses.json")


if __name__ == "__main__":
    main()
