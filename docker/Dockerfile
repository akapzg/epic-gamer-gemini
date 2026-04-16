# 使用 Python 3.12 Slim 作为基础镜像
FROM python:3.12-slim-bookworm

# 设置工作目录
WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 设置环境变量
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app/app

# 安装运行所需的系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    tini \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libdbus-1-3 \
    libxshmfence1 \
    libgtk-3-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libfreetype6 \
    libfontconfig1 \
    libx11-xcb1 \
    libxcb-glx0 \
    libxcb-dri3-0 \
    x11-utils \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml uv.lock ./

# 安装依赖
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# 复制启动脚本并设置权限
COPY docker/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 复制项目代码
COPY . .

# 预下载 camoufox 浏览器二进制文件
# 注意：Camoufox 会根据当前架构下载对应的二进制
RUN uv run camoufox fetch

# 暴露挂载点
VOLUME ["/app/app/volumes"]

# 使用 tini 处理信号，通过入口脚本启动 Xvfb
ENTRYPOINT ["/usr/bin/tini", "--", "docker-entrypoint.sh"]

# 默认运行命令
CMD ["uv", "run", "app/deploy.py"]
