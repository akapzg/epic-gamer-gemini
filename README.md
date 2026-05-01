<div align="center">

# 🎮 Epic Awesome Gamer
### (Gemini Enhanced Edition)

<img src="https://img.shields.io/static/v1?message=Python%203.12&color=3776AB&style=for-the-badge&logo=python&label=Build">
<img src="https://img.shields.io/static/v1?message=Gemini%20Flash&color=4285F4&style=for-the-badge&logo=google&label=AI%20Model">
<img src="https://img.shields.io/github/license/10000ge10000/epic-awesome-gamer?style=for-the-badge&color=orange">
<img src="https://img.shields.io/github/actions/workflow/status/10000ge10000/epic-awesome-gamer/ci.yaml?label=Auto%20Claim&style=for-the-badge&color=2ea44f">

<p class="description">
  🍷 <b>优雅、智能、全自动</b>。<br>
  专为 Docker 部署优化的 Epic Games Store 免费游戏领取机器人。
</p>

</div>

---

## 📖 项目简介

**Epic Awesome Gamer (Gemini 版)** 是一款高度定制的自动化领取工具。它在原版基础上针对 **Gemini AI** 进行了深度优化，能够通过 AI 视觉识别完美绕过 hCaptcha 验证码，并支持多架构（x86/ARM64）部署。

本项目基于原作者 [**QIN2DIM/epic-awesome-gamer**](https://github.com/QIN2DIM/epic-awesome-gamer) 进行及其他二次开发的基础上，进一步优化和修复相关bug。在此特别感谢的开源贡献！

支持 [**Finb/Bark**](https://github.com/Finb/Bark)推送领取成功消息

---

## ✨ Gemini 版核心特性

*   **🚀 极致兼容性补丁**：内置针对 Gemini SDK 的底层劫持，自动修正中转 API 路径。
*   **🔔 Bark 即时推送**：支持在游戏领取成功后，通过 Bark 发送包含游戏名称的即时通知。
*   **📸 自动化截图调试**：在领取流程的关键节点自动截取浏览器画面，方便在 Headless 模式下追踪问题。
*   **📦 深度 Docker 优化**：使用多阶段构建，预集成 Camoufox 浏览器环境，支持一键部署到 NAS 或服务器。
*   **🛡️ 智能弹窗处理**：自动识别并点击“设备不支持”和“内容警告”等阻塞性弹窗。

---

## 🚀 部署指南 (Docker)

### 1. 准备环境
确保你的宿主机已安装 Docker 和 Docker Compose。

### 2. 配置环境变量
在项目根目录下创建一个 `.env` 文件，内容如下：

```env
# 核心账号 (必须关闭 2FA)
EPIC_EMAIL=your_email@example.com
EPIC_PASSWORD=your_password

# Gemini AI 配置
GEMINI_API_KEY=sk-xxxxxx
GEMINI_BASE_URL=https://api.your-provider.com
GEMINI_MODEL=gemini-2.5-pro

# 推送配置
BARK_URL=https://api.day.app/your_bark_key

# (可选) 细分模型设置
CHALLENGE_CLASSIFIER_MODEL=gemini-3-flash-preview
```

### 3. 一键部署 (最简单)
只需在你的服务器上执行以下命令，脚本会自动为你生成配置文件并拉取最新镜像启动：
```bash
wget https://raw.githubusercontent.com/akapzg/epic-gamer-gemini/main/setup.sh
bash setup.sh
```

### 4. 多账号进阶部署
如果你有多个 Epic 账号，**强烈建议使用 Docker Compose 多开容器**。在工作目录创建 `docker-compose.yml`：
```yaml
version: '3.8'
services:
  # 账号 1
  epic-gamer-main:
    image: akapzg/epic-gamer-gemini:latest
    container_name: epic_gamer_main
    env_file: .env.user1
    volumes:
      - ./volumes/user1:/app/app/volumes
    restart: unless-stopped

  # 账号 2
  epic-gamer-alt:
    image: akapzg/epic-gamer-gemini:latest
    container_name: epic_gamer_alt
    env_file: .env.user2
    volumes:
      - ./volumes/user2:/app/app/volumes
    restart: unless-stopped
```

---

## 🛠️ 运维与调试

### 调试与记录
程序会在每次领取流程的关键节点自动保存排错资料：
*   **截图 (`volumes/screenshots/`)**：遇到无法点击、弹窗阻挡或免结账直接成功等特殊情况时，自动拍下案发现场。
*   **录像 (`volumes/record/`)**：保留完整的浏览器操作 `.webm` 录像（每次任务结束前会额外延迟 3 秒记录最终成功画面）。

> 💡 **智能存储管理**：程序自带自动清理机制。**每次定时任务启动前**，会自动删除超过 `7 天` 的旧截图，以及超过 `30 天` 的历史录像，彻底免除服务器磁盘爆满的后顾之忧！

### 数据持久化说明
请确保挂载了本地目录至容器的 `/app/app/volumes`，它包含：
*   免密登录上下文缓存（防封号核心）
*   运行日志与错误堆栈
*   调试截图与操作录像

---

## ⚠️ 免责声明

* 本项目仅供学习与技术交流使用。
* 使用自动化脚本可能违反 Epic Games 服务条款，风险自负。

---

<div align="center">
<b>Enjoy your free games! 🎮</b>
</div>
