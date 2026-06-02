#!/bin/bash
# hotels/tickets 上品工具停止脚本
# 停止后端 API（端口 8000）和前端服务（端口 3000）

set -e

echo "=========================================="
echo "  hotels/tickets 上品工具 - 停止服务"
echo "=========================================="

stopped=0

# 停止后端（端口 8000）
BACKEND_PID=$(lsof -ti:8000 2>/dev/null || true)
if [ -n "$BACKEND_PID" ]; then
    echo "  停止后端 API (端口 8000, PID: $BACKEND_PID)..."
    kill $BACKEND_PID 2>/dev/null || true
    stopped=1
else
    echo "  后端 API (端口 8000) 未运行"
fi

# 停止前端（端口 3000）
FRONTEND_PID=$(lsof -ti:3000 2>/dev/null || true)
if [ -n "$FRONTEND_PID" ]; then
    echo "  停止前端服务 (端口 3000, PID: $FRONTEND_PID)..."
    kill $FRONTEND_PID 2>/dev/null || true
    stopped=1
else
    echo "  前端服务 (端口 3000) 未运行"
fi

echo ""
if [ "$stopped" -eq 1 ]; then
    echo "  ✅ 已停止所有服务"
else
    echo "  ℹ️  没有正在运行的服务"
fi
echo "=========================================="
