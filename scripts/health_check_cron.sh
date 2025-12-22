#!/bin/bash
# AI健康检查定时任务脚本
# 每天早上9点执行

# 项目根目录（固定为 /ai-perf）
PROJECT_ROOT="/ai-perf"

# 切换到项目根目录
cd "$PROJECT_ROOT"

# 设置环境变量
export AI_PERF_ROOT="$PROJECT_ROOT"
export PATH="/usr/bin:/usr/local/bin"

# 加载环境变量（如果存在）
if [ -f "$PROJECT_ROOT/.env" ]; then
    export $(cat "$PROJECT_ROOT/.env" | grep -v '^#' | xargs)
fi

# 执行健康检查脚本
python3 "$PROJECT_ROOT/jobs/health_check.py" >> "$PROJECT_ROOT/logs/health_check.log" 2>&1

