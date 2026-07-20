#!/bin/bash
# ===================================
# AgentPay — 一键运行脚本 v2.0
# ===================================
# 使用方法:
#   ./run_all.sh              # 默认: 启动后端 + 前端
#   ./run_all.sh backend      # 只启动 FastAPI 后端
#   ./run_all.sh dashboard    # 只启动 Streamlit 前端
#   ./run_all.sh demo         # 终端演示 (需要本地链)
#   ./run_all.sh all          # 启动全部: 链 + 后端 + 前端
# ===================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

MODE="${1:-all}"

echo "========================================"
echo "  AgentPay v2.0 — 一键运行 ($MODE)"
echo "========================================"

# 安装 Python 依赖
echo ""
echo "🐍 安装 Python 依赖..."
pip3 install -r requirements.txt -q 2>/dev/null || pip3 install -r requirements.txt

# ── 需要链的模式 ──
if [ "$MODE" = "demo" ]; then
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
    echo "🚀 部署合约..."
    python3 agents/setup.py

    echo ""
    echo "🚀 运行终端演示..."
    python3 agents/run_demo.py
    exit 0
fi

# ── 后端模式 ──
if [ "$MODE" = "backend" ] || [ "$MODE" = "all" ]; then
    echo ""
    echo "🔧 启动 FastAPI 后端 (端口 8765)..."
    echo "   API 文档: http://127.0.0.1:8765/docs"
    uvicorn backend.main:app --host 0.0.0.0 --port 8765 --reload &
    BACKEND_PID=$!
    echo "   后端 PID: $BACKEND_PID"
    sleep 2
fi

# ── 前端模式 ──
if [ "$MODE" = "dashboard" ] || [ "$MODE" = "all" ]; then
    echo ""
    echo "🌐 启动 Streamlit 前端 (端口 8501)..."
    streamlit run app.py
fi

# 如果只启动后端，等待
if [ "$MODE" = "backend" ]; then
    echo ""
    echo "后端已启动，按 Ctrl+C 停止"
    wait
fi
