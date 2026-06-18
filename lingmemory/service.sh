#!/bin/bash
# 灵忆 MCP Server 启动/停止脚本
# Port: 9530
# Managed by: 灵克(lingclaude)

PIDFILE="/tmp/lingmemory-mcp.pid"
LOGFILE="/tmp/lingmemory-mcp.log"
WORKDIR="/home/ai/lingclaude"

case "$1" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
      echo "lingmemory-mcp already running (PID $(cat "$PIDFILE"))"
      exit 0
    fi
    cd "$WORKDIR"
    PYTHONPATH="$WORKDIR" nohup python3 -m lingmemory.http_server > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 2
    if kill -0 $(cat "$PIDFILE") 2>/dev/null; then
      echo "lingmemory-mcp started (PID $(cat "$PIDFILE")) on :9530"
    else
      echo "lingmemory-mcp failed to start, check $LOGFILE"
      exit 1
    fi
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      PID=$(cat "$PIDFILE")
      kill "$PID" 2>/dev/null
      rm -f "$PIDFILE"
      echo "lingmemory-mcp stopped (PID $PID)"
    else
      pkill -f 'lingmemory.http_server' 2>/dev/null
      echo "lingmemory-mcp stopped (pkill)"
    fi
    ;;
  restart)
    $0 stop
    sleep 2
    $0 start
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
      echo "lingmemory-mcp running (PID $(cat "$PIDFILE"))"
      python3 -c "import urllib.request,json; r=urllib.request.urlopen('http://127.0.0.1:9530/health',timeout=3); print(json.loads(r.read()))" 2>/dev/null
    else
      echo "lingmemory-mcp not running"
      exit 1
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
