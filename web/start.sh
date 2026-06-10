#!/bin/bash
# hotels/tickets 上品工具 - 一键启动
# 同时启动后端 (8000) 和前端 (3000)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================="
echo "  hotels/tickets 上品工具"
echo "=========================================="

# 检查依赖
echo ""
echo "[1/3] 检查 Python 依赖..."
cd "$PROJECT_DIR"
python3 -c "import fastapi, uvicorn, jose, passlib, bcrypt, qrcode" 2>/dev/null || {
    echo "  → 安装依赖..."
    python3 -m pip install -r "$PROJECT_DIR/requirements.txt" -q
    echo "  ✓ 依赖安装完成"
}

# 清理旧进程
echo "[2/3] 清理旧进程..."
lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti:3000 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

# 163 邮箱配置（用于发送验证码）
export SMTP_USER="xtongtech@163.com"
export SMTP_PASS="LC3F9TaWFfBqCxxR"

# 启动后端
echo "[3/3] 启动服务..."
echo "  → 后端: http://localhost:8000"
cd "$PROJECT_DIR"
python3 -m uvicorn web.backend.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# 启动前端
echo "  → 前端: http://localhost:3000"
cd "$SCRIPT_DIR/frontend"
python3 -m http.server 3000 &
FRONTEND_PID=$!

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
    wait $BACKEND_PID 2>/dev/null
    wait $FRONTEND_PID 2>/dev/null
    echo "已停止所有服务"
    exit 0
}
trap cleanup SIGINT SIGTERM

# 等待子进程
wait
