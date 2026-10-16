#!/bin/bash
set -e

# 设置 Xvfb 分辨率
XVFBARGS="-screen 0 1920x1080x24 -ac +extension GLX +render -noreset"

# 清理可能残留的 Xvfb 锁文件，防止容器异常退出重启后报 "Server is already active for display 99"
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

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
