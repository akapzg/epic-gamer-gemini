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

---

## ✨ Gemini 版核心特性

*   **🚀 极致兼容性补丁**：内置针对 Gemini SDK 的底层劫持，自动修正中转 API 路径，并将图片上传转换为无损 **PNG Base64**，彻底解决 400 报错。
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

### 3. 一键构建与启动
```bash
# 执行构建脚本
chmod +x setup.sh
./setup.sh

# 启动容器
cd docker
docker compose up -d
```

---

## 🛠️ 运维与调试

### 查看日志
```bash
docker logs -f epic-gamer-gemini
```

### 调试截图
程序会自动将截图保存至 `./docker/volumes/screenshots/`。如果你发现游戏“领取成功”但库里没有，请通过截图排查原因。
> 💡 **提示**：建议定期手动清理截图文件夹，以释放磁盘空间。

### 挂载路径说明
*   日志：`./docker/volumes/logs/`
*   用户数据：`./docker/volumes/user_data/`

---

## ⚠️ 免责声明

* 本项目仅供学习与技术交流使用。
* 使用自动化脚本可能违反 Epic Games 服务条款，风险自负。

---

<div align="center">
<b>Enjoy your free games! 🎮</b>
</div>
