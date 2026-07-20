/**
 * AgentPay 合约测试
 *
 * 测试场景：Alice（金融数据 Agent）注册"财报查询"服务
 *           Bob（股票分析 Agent）付款调用该服务
 *           Alice 交付数据 → Bob 确认 → 钱到 Alice 账户
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("AgentPay — 完整支付流程", function () {

  let registry, escrow, reputation;
  let alice, bob, charlie;

  const SERVICE_NAME = "financial_report_query";
  const SERVICE_PRICE = ethers.parseEther("0.01"); // 0.01 ETH

  beforeEach(async function () {
    [alice, bob, charlie] = await ethers.getSigners();

    // 部署合约
    const Registry = await ethers.getContractFactory("ServiceRegistry");
    registry = await Registry.deploy();
    await registry.waitForDeployment();

    const Escrow = await ethers.getContractFactory("EscrowPayment");
    escrow = await Escrow.deploy(await registry.getAddress());
    await escrow.waitForDeployment();

    const Reputation = await ethers.getContractFactory("Reputation");
    reputation = await Reputation.deploy();
    await reputation.waitForDeployment();
  });

  describe("1. 服务注册", function () {
    it("Alice 可以注册一个财报查询服务", async function () {
      const tx = await registry.connect(alice).registerService(
        SERVICE_NAME,
        "查询美股上市公司的财报核心指标",
        SERVICE_PRICE
      );
      await tx.wait();

      const count = await registry.getServiceCount();
      expect(count).to.equal(1);
    });

    it("查询服务时返回正确的价格", async function () {
      await registry.connect(alice).registerService(
        SERVICE_NAME, "美股财报查询", SERVICE_PRICE
      );

      const services = await registry.getService(SERVICE_NAME);
      expect(services.length).to.equal(1);
      expect(services[0].price).to.equal(SERVICE_PRICE);
      expect(services[0].provider).to.equal(alice.address);
    });
  });

  describe("2. 支付流程", function () {
    beforeEach(async function () {
      // 先注册服务
      await registry.connect(alice).registerService(
        SERVICE_NAME, "美股财报查询", SERVICE_PRICE
      );
    });

    it("Bob 可以付款请求服务", async function () {
      const tx = await escrow.connect(bob).requestService(
        alice.address, 0, { value: SERVICE_PRICE }
      );
      await tx.wait();

      const request = await escrow.getRequest(0);
      expect(request.consumer).to.equal(bob.address);
      expect(request.provider).to.equal(alice.address);
      expect(request.amount).to.equal(SERVICE_PRICE);
      expect(request.state).to.equal(0); // Pending
    });

    it("Alice 交付后 Bob 确认 → Alice 收到钱", async function () {
      // Bob 付款
      await escrow.connect(bob).requestService(
        alice.address, 0, { value: SERVICE_PRICE }
      );

      const aliceBalanceBefore = await ethers.provider.getBalance(alice.address);

      // Alice 交付数据（模拟：数据哈希 = keccak256("AAPL Q4 2024 EPS: $2.35")）
      const dataHash = ethers.keccak256(ethers.toUtf8Bytes("AAPL Q4 2024 EPS: $2.35"));
      await escrow.connect(alice).deliverResult(0, dataHash);

      // Bob 确认
      await escrow.connect(bob).confirmDelivery(0);

      const aliceBalanceAfter = await ethers.provider.getBalance(alice.address);
      // Alice 收到 SERVICE_PRICE，扣除 deliverResult 的 Gas 费
      const balanceDiff = aliceBalanceAfter - aliceBalanceBefore;
      expect(balanceDiff).to.be.closeTo(SERVICE_PRICE, ethers.parseEther("0.001"));
    });

    it("如果 Alice 超时未交付 → Bob 能退款", async function () {
      await escrow.connect(bob).requestService(
        alice.address, 0, { value: SERVICE_PRICE }
      );

      const bobBalanceBefore = await ethers.provider.getBalance(bob.address);

      // 模拟时间流逝到超时
      await ethers.provider.send("evm_increaseTime", [30 * 60 + 1]); // 30分钟 + 1秒
      await ethers.provider.send("evm_mine");

      // Bob 退款
      const tx = await escrow.connect(bob).refund(0);
      const receipt = await tx.wait();

      // 计算 Gas 费用
      const gasCost = receipt.gasUsed * receipt.gasPrice;

      const bobBalanceAfter = await ethers.provider.getBalance(bob.address);
      // Bob 拿回金额，仅损失 Gas 费
      expect(bobBalanceAfter + gasCost - bobBalanceBefore).to.equal(SERVICE_PRICE);
    });
  });

  describe("3. 声誉系统", function () {
    it("Bob 可以给 Alice 打 5 星", async function () {
      // 先完成一笔交易
      await registry.connect(alice).registerService(
        SERVICE_NAME, "美股财报查询", SERVICE_PRICE
      );
      await escrow.connect(bob).requestService(
        alice.address, 0, { value: SERVICE_PRICE }
      );
      await escrow.connect(alice).deliverResult(
        0, ethers.keccak256(ethers.toUtf8Bytes("data"))
      );
      await escrow.connect(bob).confirmDelivery(0);

      // Bob 打分
      await reputation.connect(bob).rateProvider(alice.address, 5);

      const [avg, count, jobs] = await reputation.getRating(alice.address);
      expect(avg).to.equal(5);
      expect(count).to.equal(1);
      expect(jobs).to.equal(1);
    });
  });
});
