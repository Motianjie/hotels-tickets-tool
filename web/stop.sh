#!/bin/bash
# hotels/tickets 上品工具停止脚本
# 停止后台运行的后端 API 服务

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="$PROJECT_DIR/.backend.pid"

PORT="${1:-80}"

echo "=========================================="
echo "  hotels/tickets 上品工具 - 停止服务"
echo "=========================================="

stopped=0

# 优先通过 PID 文件停止
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "  停止后端服务 (PID: $PID)..."
        kill "$PID" 2>/dev/null || true
        sleep 1
        # 如果还没死，强制杀掉
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
        stopped=1
    else
        echo "  PID 文件中的进程 ($PID) 已不存在"
    fi
    rm -f "$PID_FILE"
fi

# 兜底：通过端口清理
BACKEND_PID=$(lsof -ti:$PORT 2>/dev/null || true)
if [ -n "$BACKEND_PID" ]; then
    echo "  清理端口 $PORT 残留进程 (PID: $BACKEND_PID)..."
    kill -9 $BACKEND_PID 2>/dev/null || true
    stopped=1
fi

# 兼容旧端口
OLD_PID=$(lsof -ti:8000 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    echo "  清理旧版端口 8000 (PID: $OLD_PID)..."
    kill -9 $OLD_PID 2>/dev/null || true
    stopped=1
fi

FRONTEND_PID=$(lsof -ti:3000 2>/dev/null || true)
if [ -n "$FRONTEND_PID" ]; then
    echo "  清理前端端口 3000 (PID: $FRONTEND_PID)..."
    kill -9 $FRONTEND_PID 2>/dev/null || true
    stopped=1
fi

echo ""
if [ "$stopped" -eq 1 ]; then
    echo "  ✅ 已停止所有服务"
else
    echo "  ℹ️  没有正在运行的服务"
fi
echo "=========================================="
