#!/bin/bash
set -e

# 设置 Xvfb 分辨率
XVFBARGS="-screen 0 1920x1080x24 -ac +extension GLX +render -noreset"

# 启动 Xvfb
Xvfb :99 $XVFBARGS &
export DISPLAY=:99

# 等待 Xvfb 启动成功
for i in {1..10}; do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        echo "✅ Xvfb is ready on DISPLAY :99"
        break
    fi
    echo "⌛ Waiting for Xvfb..."
    sleep 1
done

# 运行主程序
exec "$@"
