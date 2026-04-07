#!/bin/bash

# Install Resource Monitor Dashboard as Systemd Service

echo "========================================="
echo "🎯 安装资源监控面板为 Systemd 服务"
echo "========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ 请使用 root 权限运行此脚本"
    echo "用法: sudo $0"
    exit 1
fi

# Get script directory
SCRIPT_DIR="/home/ai/LingClaude/scripts"

# Check if service file exists
if [ ! -f "$SCRIPT_DIR/ling-resource-monitor.service" ]; then
    echo "❌ 服务文件未找到：$SCRIPT_DIR/ling-resource-monitor.service"
    exit 1
fi

# Check if dashboard script exists
if [ ! -f "$SCRIPT_DIR/resource_monitor_dashboard.py" ]; then
    echo "❌ 仪表板脚本未找到：$SCRIPT_DIR/resource_monitor_dashboard.py"
    exit 1
fi

# Check dependencies
echo "🔍 检查依赖..."

if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 未安装"
    exit 1
fi

echo "✅ Python 3 已安装"

# Install Python dependencies
echo ""
echo "📦 安装 Python 依赖..."
pip3 install fastapi uvicorn psutil jinja2 2>/dev/null

if [ $? -eq 0 ]; then
    echo "✅ 依赖安装成功"
else
    echo "⚠️ 依赖安装可能失败，请手动检查"
fi

# Copy service file to systemd
echo ""
echo "📋 复制服务文件到 systemd..."
cp "$SCRIPT_DIR/ling-resource-monitor.service" /etc/systemd/system/

if [ $? -eq 0 ]; then
    echo "✅ 服务文件已复制到 /etc/systemd/system/"
else
    echo "❌ 服务文件复制失败"
    exit 1
fi

# Reload systemd
echo ""
echo "🔄 重新加载 systemd 配置..."
systemctl daemon-reload

if [ $? -eq 0 ]; then
    echo "✅ systemd 配置已重新加载"
else
    echo "❌ systemd 配置重新加载失败"
    exit 1
fi

# Enable service to start on boot
echo ""
echo "🔌 启用服务开机自启..."
systemctl enable ling-resource-monitor.service

if [ $? -eq 0 ]; then
    echo "✅ 服务已启用开机自启"
else
    echo "❌ 服务启用失败"
    exit 1
fi

# Start service
echo ""
echo "🚀 启动服务..."
systemctl start ling-resource-monitor.service

if [ $? -eq 0 ]; then
    echo "✅ 服务已启动"
else
    echo "❌ 服务启动失败"
    echo "运行以下命令查看错误："
    echo "sudo journalctl -u ling-resource-monitor.service -n 50"
    exit 1
fi

# Wait for service to start
echo ""
echo "⏳ 等待服务启动..."
sleep 3

# Check service status
echo ""
echo "📊 服务状态："
systemctl status ling-resource-monitor.service --no-pager

# Get service PID
SERVICE_PID=$(systemctl show -p MainPID ling-resource-monitor.service | cut -d= -f2)
if [ -n "$SERVICE_PID" ] && [ "$SERVICE_PID" != "0" ]; then
    echo ""
    echo "✅ 服务运行中 (PID: $SERVICE_PID)"
else
    echo ""
    echo "⚠️ 服务的 PID 未找到，可能启动失败"
fi

echo ""
echo "========================================="
echo "🎯 安装完成！"
echo "========================================="
echo ""
echo "📊 仪表板地址：http://localhost:8090"
echo "📝 查看日志：sudo journalctl -u ling-resource-monitor.service -f"
echo "⏹️  停止服务：sudo systemctl stop ling-resource-monitor.service"
echo "▶️  重启服务：sudo systemctl restart ling-resource-monitor.service"
echo "🔌 禁用开机启动：sudo systemctl disable ling-resource-monitor.service"
echo "🗑️  删除服务：sudo systemctl disable ling-resource-monitor.service && sudo rm /etc/systemd/system/ling-resource-monitor.service && sudo systemctl daemon-reload"
echo ""
echo "========================================="
