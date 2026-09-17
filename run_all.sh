#!/bin/bash
# ===================================
# AgentFleet — 一键运行脚本 v3.1
# ===================================
# 使用方法:
#   ./run_all.sh              # 默认: 启动全部 (链 + 后端 + 前端)
#   ./run_all.sh all          # 同默认: 链 + 后端 + 前端
#   ./run_all.sh chain        # 只起本地链 + 部署合约 (写 chain_addresses.json)
#   ./run_all.sh backend      # 只启动 FastAPI 后端
#   ./run_all.sh dashboard    # 只启动 Streamlit 前端
#   ./run_all.sh demo         # 终端演示 (需要本地链)
# ===================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

MODE="${1:-all}"

echo "========================================"
echo "  AgentFleet v3.1 — 一键运行 ($MODE)"
echo "========================================"

# ── Python 环境：自动创建/复用 .venv ──
# 注意: 本机 pip3 指向系统 Python 3.9，而代码需要 3.10+（X | None 语法），
#       故统一用 python3 建 venv，所有运行都走 .venv。
PY="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo ""
    echo "🐍 创建虚拟环境 .venv ..."
    python3 -m venv "$PROJECT_DIR/.venv"
    "$PY" -m pip install --upgrade pip -q
fi

# 依赖未装时才安装（重复启动时跳过，加速）
if ! "$PY" -c "import streamlit, fastapi, web3, sqlalchemy" >/dev/null 2>&1; then
    echo ""
    echo "🐍 安装 Python 依赖..."
    "$PY" -m pip install -r requirements.txt -q
fi

# ── 链初始化 ──
start_chain() {
    # Hardhat 2.28 需要这些目录，否则写 vars.json / analytics.json 会报 ENOENT
    mkdir -p /tmp/hardhat-config /tmp/hardhat-data

    echo ""
    echo "📦 安装 JS 依赖..."
    npm install --silent 2>/dev/null || npm install

    echo ""
    echo "🔨 编译合约..."
    npx hardhat compile

    echo ""
    echo "⛓️  启动本地链..."
    if ! curl -s http://127.0.0.1:8545 -X POST -H "Content-Type: application/json" --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' > /dev/null 2>&1; then
        npx hardhat node > /tmp/hardhat-node.log 2>&1 &
        for i in $(seq 1 30); do
            if curl -s http://127.0.0.1:8545 -X POST -H "Content-Type: application/json" --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' > /dev/null 2>&1; then
                echo "  ✅ 节点已就绪"
                break
            fi
            sleep 1
        done
    else
        echo "  ✅ 节点已在运行"
    fi

    echo ""
    echo "🚀 部署合约 (写入 chain_addresses.json)..."
    npx hardhat run scripts/deploy.js --network localhost
}

# ── chain 模式：只起链 ──
if [ "$MODE" = "chain" ]; then
    start_chain
    echo ""
    echo "✅ 链已就绪，合约地址已写入 chain_addresses.json"
    exit 0
fi

# ── demo 模式：起链 + 后端 + 终端演示 ──
if [ "$MODE" = "demo" ]; then
    start_chain
    echo ""
    echo "🔧 启动 FastAPI 后端 (端口 8765)..."
    "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port 8765 > /tmp/agentfleet-backend.log 2>&1 &
    sleep 3
    echo ""
    echo "🚀 运行终端演示..."
    "$PY" agents/run_demo.py
    exit 0
fi

# ── all 模式：先起链 ──
if [ "$MODE" = "all" ]; then
    start_chain
fi

# ── 后端模式 ──
if [ "$MODE" = "backend" ] || [ "$MODE" = "all" ]; then
    echo ""
    echo "🔧 启动 FastAPI 后端 (端口 8765)..."
    echo "   API 文档: http://127.0.0.1:8765/docs"
    "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port 8765 --reload &
    BACKEND_PID=$!
    echo "   后端 PID: $BACKEND_PID"
    sleep 2
fi

# ── 前端模式 ──
if [ "$MODE" = "dashboard" ] || [ "$MODE" = "all" ]; then
    echo ""
    echo "🌐 启动 Streamlit 前端 (端口 8501)..."
    "$PY" -m streamlit run app.py
fi

# 如果只启动后端，等待
if [ "$MODE" = "backend" ]; then
    echo ""
    echo "后端已启动，按 Ctrl+C 停止"
    wait
fi
