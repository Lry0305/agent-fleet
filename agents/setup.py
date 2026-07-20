#!/usr/bin/env python3
"""
部署 + 配置自动化
================
运行 Hardhat deploy 脚本 → 解析合约地址 → 自动更新 config.py
"""

import subprocess
import re
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "agents", "config.py")


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


def parse_addresses(output: str) -> dict:
    """从部署输出中解析合约地址"""
    addresses = {}

    patterns = {
        "SERVICE_REGISTRY_ADDRESS": r"ServiceRegistry\s+部署地址:\s+(0x[a-fA-F0-9]{40})",
        "ESCROW_PAYMENT_ADDRESS": r"EscrowPayment\s+部署地址:\s+(0x[a-fA-F0-9]{40})",
        "REPUTATION_ADDRESS": r"Reputation\s+部署地址:\s+(0x[a-fA-F0-9]{40})",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if match:
            addresses[key] = match.group(1)
        else:
            print(f"⚠️ 未能解析 {key}，请手动填入 config.py")

    return addresses


def update_config(addresses: dict):
    """自动更新 config.py 中的合约地址"""
    if not addresses:
        print("⚠️ 没有地址需要更新")
        return

    with open(CONFIG_PATH, "r") as f:
        content = f.read()

    for key, addr in addresses.items():
        # 替换空字符串默认值
        pattern = rf'({key}\s*=\s*os\.getenv\(\s*"{key}"\s*,\s*)\"\"'
        replacement = rf'\1"{addr}"'
        old_len = len(content)
        content = re.sub(pattern, replacement, content)
        if len(content) != old_len:
            print(f"  ✅ {key} = {addr}")
        else:
            print(f"  ⚠️ {key} 未更新（可能已有值）")

    with open(CONFIG_PATH, "w") as f:
        f.write(content)


def main():
    print("=" * 50)
    print("  AgentPay — 合约部署 & 配置")
    print("=" * 50)

    output = deploy_contracts()
    addresses = parse_addresses(output)

    if addresses:
        print(f"\n📋 解析到 {len(addresses)} 个合约地址:")
        for k, v in addresses.items():
            print(f"   {k}: {v}")

        update_config(addresses)
        print(f"\n✅ 配置已更新: {CONFIG_PATH}")
    else:
        print("\n⚠️ 未能解析合约地址，请手动更新 agents/config.py")


if __name__ == "__main__":
    main()
