# AgentFleet 部署上线指南

> 目标：把「只在本机跑」的 AgentFleet 变成有真实公网地址，供现场演示。分两块：后端 FastAPI + 前端 Streamlit。

## 架构

```
浏览器 ──► Streamlit Community Cloud (前端 app.py)
              │  HTTP（requests，服务器端出站，无 CORS 问题）
              ▼
         Render (后端 FastAPI + SQLite)
              │  连不到链时自动降级 off-chain 记账（backend/chain.py chain_available()）
              ▼
         （可选）本地 Hardhat / Sepolia 测试网
```

- 前端是 Streamlit 多页应用，跑在 **Streamlit Community Cloud**（免费，连 GitHub 自动部署）。
- 后端是 FastAPI，跑在 **Render**（免费 web service，Dockerfile 构建）。
- 前端通过 `AGENTFLEET_API` 环境变量知道后端地址（`ui/api.py` 已支持，默认 `127.0.0.1:8765`）。
- 链上结算：云后端连不到本机 8545，`chain_available()` 自动降级 SQLite off-chain，演示不受影响。

## 一、部署后端（Render）

### 方式 A：Blueprint 一键部署（推荐）

1. 确认本仓库已推到 GitHub（`Lry0305/agent-fleet`）。
2. 打开 Render 控制台 → **New → Blueprint**，连接该仓库。
3. Render 读取根目录 `render.yaml`，自动创建 `agentfleet-api` web service（Docker 构建 + `/health` 健康检查）。
4. 构建完成后，记下后端地址：`https://agentfleet-api.onrender.com`。

### 方式 B：手动创建

1. Render → **New → Web Service**，连接仓库，Runtime 选 **Docker**。
2. 环境变量默认已由 Dockerfile 设好（`DATABASE_URL=sqlite:////data/agentfleet.db`），可不动。
3. 部署。

### 关于 SQLite 持久化

- Render 免费层文件系统是**临时盘**：服务重启数据还在，但**重新部署会清空**。
- 一天演示无所谓——启动时自动建表 + 播种「演示老板」账号（`boss.ensure_demo_boss()`）。
- 要长期留数据：在 Render 给服务加一个 **Persistent Disk**，挂载到 `/data`（与 `DATABASE_URL` 一致），无需改代码。

### 唤醒提醒

- Render 免费服务 15 分钟无请求会**休眠**，第一次访问要等 30–60 秒冷启动。
- **现场演示前**先点一次 `https://agentfleet-api.onrender.com/health` 把它唤醒。

## 二、部署前端（Streamlit Community Cloud）

1. 打开 https://share.streamlit.io → 登录 → **New app**，选择仓库 `Lry0305/agent-fleet`，入口 `app.py`。
2. 在 App 的 **Settings → Secrets** 里加一条（`ui/api.py` 同时兼容环境变量和 `st.secrets`）：

   ```toml
   AGENTFLEET_API = "https://agentfleet-api.onrender.com"
   ```

3. 部署。前端从服务器端调用后端，无 CORS 问题。

## 三、部署后的现场演示流程

1. 打开前端 URL，左侧「一键演示登录」（演示老板：`13800000000` / `demo123456`）。
2. 「充值」页选支付宝 → 输入金额 → 生成 mock 二维码 → 模拟支付成功（真实支付宝需商户资质，这里是渠道抽象 + mock 网关）。
3. 「我的 Agent」页创建 2 个 provider，各设一个 service_name（如 `stock_analysis`、`report_writing`）。
4. 「经营台」一句话派活（或 `/服务名称` 指定），任务看板实时显示进度 → 交付 → 自动结算。
5. 「账本」看流水，「我的技能」看技能包装配。

> 演示前记得先唤醒后端（见上）。

## 四、链上结算（可选，非本次重点）

- 默认 off-chain 记账（SQLite），够演示。
- 要展示真实链上结算：把 `RPC_URL` / `SERVICE_REGISTRY_ADDRESS` / `ESCROW_PAYMENT_ADDRESS` / `REPUTATION_ADDRESS` 环境变量指向 Sepolia 测试网 + 已部署合约。演示用不上主网。
