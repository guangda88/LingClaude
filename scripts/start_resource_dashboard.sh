#!/bin/bash

# Resource Monitor Dashboard Launcher

echo "========================================="
echo "🎯 灵字辈资源分配监控面板"
echo "========================================="
echo ""

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed"
    exit 1
fi

# Check if required packages are installed
echo "🔍 Checking dependencies..."

if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "⚠️  FastAPI is not installed, installing..."
    pip3 install fastapi uvicorn psutil jinja2
fi

if ! python3 -c "import uvicorn" 2>/dev/null; then
    echo "⚠️  Uvicorn is not installed, installing..."
    pip3 install uvicorn
fi

if ! python3 -c "import psutil" 2>/dev/null; then
    echo "⚠️  Psutil is not installed, installing..."
    pip3 install psutil
fi

if ! python3 -c "import jinja2" 2>/dev/null; then
    echo "⚠️  Jinja2 is not installed, installing..."
    pip3 install jinja2
fi

echo "✅ All dependencies are installed"
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if the dashboard script exists
if [ ! -f "resource_monitor_dashboard.py" ]; then
    echo "❌ resource_monitor_dashboard.py not found in $SCRIPT_DIR"
    exit 1
fi

# Start the dashboard
echo "🚀 Starting Resource Monitor Dashboard..."
echo ""
echo "📊 Dashboard will be available at: http://localhost:8090"
echo "💡 Press Ctrl+C to stop the dashboard"
echo ""
echo "========================================="
echo ""

# Run the dashboard
python3 resource_monitor_dashboard.py
