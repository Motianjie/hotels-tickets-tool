#!/bin/bash
# hotels/tickets 上品工具 - 后台启动
# 启动后端 API（默认端口 80），前端静态文件由 FastAPI 直接托管
# 日志输出到 ../logs/backend.log

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="$PROJECT_DIR/.backend.pid"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/backend.log"

PORT="${1:-80}"

echo "=========================================="
echo "  hotels/tickets 上品工具"
echo "=========================================="

# 创建日志目录
mkdir -p "$LOG_DIR"

# 检查依赖
echo ""
echo "[1/4] 检查 Python 依赖..."
cd "$PROJECT_DIR"
python3 -c "import fastapi, uvicorn, jose, passlib, bcrypt, qrcode" 2>/dev/null || {
    echo "  → 安装依赖..."
    python3 -m pip install -r "$PROJECT_DIR/requirements.txt" -q
    echo "  ✓ 依赖安装完成"
}

# 清理旧进程
echo "[2/4] 清理旧进程..."
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "  停止旧进程 (PID: $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$PID_FILE"
fi
lsof -ti:$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti:3000 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

# 163 邮箱配置（用于发送验证码）
export SMTP_USER="xtongtech@163.com"
export SMTP_PASS="GW2QwgxUS94en6Gw"

# 后台启动
echo "[3/4] 后台启动服务..."
cd "$PROJECT_DIR"
if [ "$PORT" = "80" ]; then
    echo "  → 服务地址: http://YOUR_DOMAIN_OR_IP"
    echo "  → 端口: 80（需要 root 权限，请使用 sudo 运行或执行: sudo setcap 'cap_net_bind_service=+ep' \$(which python3)）"
fi

nohup python3 -m uvicorn web.backend.main:app --host 0.0.0.0 --port $PORT >> "$LOG_FILE" 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > "$PID_FILE"

# 等待确认启动成功
sleep 2
if kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "[4/4] ✅ 启动成功！"
else
    echo "[4/4] ❌ 启动失败，请查看日志: tail -f $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi

echo ""
echo "=========================================="
echo "  访问地址: http://localhost:$PORT"
echo "  API 文档: http://localhost:$PORT/docs"
echo "  运行日志: tail -f $LOG_FILE"
echo "  停止服务: ./web/stop.sh"
echo "=========================================="
