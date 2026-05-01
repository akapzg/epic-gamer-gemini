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

# 初始化目录与配置
mkdir -p volumes

if [ ! -f ".env" ]; then
    echo "📄 正在创建默认配置文件 .env..."
    cat <<EOF > .env
EPIC_EMAIL=your_email@example.com
EPIC_PASSWORD=your_password
GEMINI_API_KEY=your_gemini_api_key
GEMINI_BASE_URL=https://api.syd.thepzg.site
BARK_URL=
EOF
    echo "⚠️ 请使用编辑器 (如 nano .env) 填入你的 EPIC 账号和 GEMINI 令牌！"
    exit 0
fi

if [ ! -f "docker-compose.yml" ]; then
    echo "🐳 正在创建 docker-compose.yml..."
    cat <<EOF > docker-compose.yml
version: '3.8'

services:
  epic-gamer:
    image: akapzg/epic-gamer-gemini:latest
    container_name: epic_gamer
    env_file:
      - .env
    volumes:
      - ./volumes:/app/app/volumes
    restart: unless-stopped
EOF
fi

# 启动容器
echo "🚀 正在拉取最新镜像并启动容器..."
$DOCKER_COMPOSE pull
$DOCKER_COMPOSE up -d

echo "✅ 部署完成！"
echo "你可以使用以下命令查看运行日志："
echo "   $DOCKER_COMPOSE logs -f"
echo "--------------------------------------------------------"

