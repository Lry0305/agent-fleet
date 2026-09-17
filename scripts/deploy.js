// deploy.js —— 部署所有合约到本地测试链
const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  // 1. 部署 ServiceRegistry
  const ServiceRegistry = await hre.ethers.getContractFactory("ServiceRegistry");
  const registry = await ServiceRegistry.deploy();
  await registry.waitForDeployment();
  console.log(`ServiceRegistry 部署地址: ${await registry.getAddress()}`);

  // 2. 部署 EscrowPayment（传入 Registry 地址）
  const EscrowPayment = await hre.ethers.getContractFactory("EscrowPayment");
  const escrow = await EscrowPayment.deploy(await registry.getAddress());
  await escrow.waitForDeployment();
  console.log(`EscrowPayment 部署地址: ${await escrow.getAddress()}`);

  // 3. 部署 Reputation
  const Reputation = await hre.ethers.getContractFactory("Reputation");
  const rep = await Reputation.deploy();
  await rep.waitForDeployment();
  console.log(`Reputation 部署地址: ${await rep.getAddress()}`);

  // 4. 写入 chain_addresses.json（backend/config.py 与 agents/config.py 启动时自动读取）
  const addresses = {
    serviceRegistry: await registry.getAddress(),
    escrowPayment: await escrow.getAddress(),
    reputation: await rep.getAddress(),
  };
  const outPath = path.join(__dirname, "..", "chain_addresses.json");
  fs.writeFileSync(outPath, JSON.stringify(addresses, null, 2));
  console.log(`✅ 合约地址已写入: ${outPath}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
