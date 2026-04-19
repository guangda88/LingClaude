#!/bin/bash
# EXP-001 批处理脚本
# 并行运行多个实验组

set -e

echo "Starting EXP-001 experiments..."

# 创建日志目录
LOG_DIR="logs/EXP-001"
mkdir -p "$LOG_DIR"

# 并行运行实验组
# 组A
python3 -m experiments.runner EXP-001 A > "$LOG_DIR/group_A.log" 2>&1 &
PID_A=$!

# 组B
python3 -m experiments.runner EXP-001 B > "$LOG_DIR/group_B.log" 2>&1 &
PID_B=$!

# 组C
python3 -m experiments.runner EXP-001 C > "$LOG_DIR/group_C.log" 2>&1 &
PID_C=$!

echo "Started all groups. PIDs: $PID_A, $PID_B, $PID_C"
echo "Monitoring progress..."

# 等待所有组完成
wait $PID_A
echo "✓ Group A completed"

wait $PID_B
echo "✓ Group B completed"

wait $PID_C
echo "✓ Group C completed"

echo "All groups completed. Generating reports..."
python3 -m experiments.analyzer EXP-001

echo "✓ EXP-001 completed"
