#!/bin/bash
set -e

echo "🎮 Epic Awesome Gamer (Gemini Edition) - 一键部署工具"
echo "--------------------------------------------------------"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ 错误: 未找到 Docker，请先安装 Docker。"
    exit 1
fi

# 检查 Docker Compose
DOCKER_COMPOSE="docker compose"
if ! $DOCKER_COMPOSE version &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
    if ! $DOCKER_COMPOSE version &> /dev/null; then
        echo "❌ 错误: 未找到 docker compose 或 docker-compose。"
        exit 1
    fi
fi

# 确认环境变量设置
if [ ! -f "docker/.env" ]; then
    echo "📄 创建默认配置文件 docker/.env..."
    cp .env.example docker/.env 2>/dev/null || touch docker/.env
    echo "⚠️ 请记得在 docker/.env 中填入你的 EPIC_EMAIL, EPIC_PASSWORD 和 BARK_URL！"
fi

# 构建镜像
echo "🛠️ 正在构建本地镜像 epic-gamer-gemini:latest..."
docker build -f docker/Dockerfile -t epic-gamer-gemini:latest .

echo "✅ 镜像构建完成！"
echo "--------------------------------------------------------"
echo "🚀 提示：你可以直接运行以下命令启动容器："
echo "   cd docker && $DOCKER_COMPOSE up -d"
echo "--------------------------------------------------------"
