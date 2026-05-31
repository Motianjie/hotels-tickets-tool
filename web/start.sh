#!/bin/bash
# hotels/tickets 上品工具启动脚本
# 启动后端 API 和前端静态文件服务

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo "=========================================="
echo "  hotels/tickets 上品工具"
echo "=========================================="

# 检查 Python 依赖
echo ""
echo "[1/3] 检查 Python 依赖..."
cd "$BACKEND_DIR"
pip3 install -r requirements.txt -q 2>/dev/null || pip install -r requirements.txt -q

# 启动后端
echo "[2/3] 启动后端 API (端口 8000)..."
python3 main.py &
BACKEND_PID=$!
echo "  后端 PID: $BACKEND_PID"

# 等待后端就绪
sleep 2

# 启动前端静态文件服务
echo "[3/3] 启动前端服务 (端口 3000)..."
cd "$FRONTEND_DIR"
python3 -m http.server 3000 &
FRONTEND_PID=$!
echo "  前端 PID: $FRONTEND_PID"

echo ""
echo "=========================================="
echo "  ✅ 启动完成！"
echo ""
echo "  前端页面: http://localhost:3000"
echo "  后端 API: http://localhost:8000"
echo "  API 文档: http://localhost:8000/docs"
echo ""
echo "  按 Ctrl+C 停止所有服务"
echo "=========================================="

# 捕获退出信号，清理子进程
cleanup() {
    echo ""
    echo "正在停止服务..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    echo "已停止所有服务"
    exit 0
}
trap cleanup SIGINT SIGTERM

# 等待子进程
wait
