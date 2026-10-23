# Epic Gamer Gemini — 项目架构与工程避坑指南 (AGENTS.md)

本文件为 AI 编程助手提供 `epic-gamer-gemini` 的完整上下文、架构契约与历史排障经验（覆盖 `v1.1.8` 至 `v1.1.20` 全部演进）。修改任何代码前必须严格遵守本指南中的红线约束。

---

## 1. 项目概览与核心文件地图

本项目基于 **Camoufox (反指纹 Firefox) + Playwright + hcaptcha-challenger (Gemini 视觉求解)**，在 Docker 容器（Xvfb 虚拟显示）中实现 Epic Games 每周免费游戏的无人值守自动鉴权、领取、核验与 Bark 通知推送。

| 文件路径 | 核心职责 |
| :--- | :--- |
| `app/deploy.py` | 入口与 APScheduler 定时调度器、防风控随机延迟（10~600s）、Camoufox 1080p 持久化上下文与 WebM 录屏管理 |
| `app/settings.py` | Pydantic 环境变量配置 + **Gemini 代理补丁** + **hCaptcha 深度穿透与容错猴子补丁 (`_apply_gemini_proxy_patch`)** |
| `app/services/epic_authorization_service.py` | 账号会话严格核验 (`invoke`)、登录全流程 (`_login`)、`Welcome back` 记住账号卡片点击、阶段性 hCaptcha 拦截与 2FA 弹窗跳过 |
| `app/services/epic_games_service.py` | 周免促销 API 解析 (`get_promotions`)、18+ 年龄门禁与重登恢复 (`_handle_age_gate_and_takeover`)、商品页状态判断与点击 (`add_promotion_to_cart`)、即时结账 (`_handle_instant_checkout`) 与入库实锤终验 (`_verify_library_status`) |
| `app/utils.py` | Loguru 日志初始化与 Bark 推送工具 |
| `docker/docker-entrypoint.sh` | 清理 `/tmp/.X99-lock` 残留锁、启动 Xvfb `:99`、拉起 `python app/deploy.py`（**必须保持 LF 换行符**） |

---

## 2. 端到端核心工作流

1. **启动与防风控错峰 (`deploy.py`)**：
   - 容器启动时立即执行一次领取（无随机延迟）；
   - 定时任务（北京时间周四 `23:30`、每日 `12:00`）启动前随机休眠 `10~600` 秒；
   - 自动清理超过 30 天的 `.webm` 录像与 `.png` 截图。
2. **单标签页贯穿始终 (`deploy.py`)**：
   - 使用 `browser.pages[0]` 单一 `page` 实例贯穿鉴权与商城领取全过程，**严禁新建/关闭多标签页**，防止跨 Tab 导致 Cookie/Session 状态不同步。
3. **初始会话严格核验 (`EpicAuthorization.invoke`)**：
   - 导航至受保护的 `https://www.epicgames.com/account/personal`；
   - 轮询 6 秒，检测是否被重定向至 `/id/login`，并验证是否真实渲染出用户邮箱表单（`input[name='email']` 等）或有效 `<egs-navigation isloggedin="true" displayname="...">`；
   - 若失效则调用 `_login()` 登录。
4. **登录全流程 (`EpicAuthorization._login`)**：
   - 若不在 `/id/login`，先访问 `https://www.epicgames.com/account/personal?lang=en-US&productName=egs&sessionInvalidated=true` 触发统一登录跳转；
   - **分支 A（`Welcome back` 记住账号界面）**：精准匹配交互节点（`button, [role='button'], a, li, [tabindex='0']` 或文本节点）点击已存账号卡片；若 8 秒内直接跳转则登录完成，若未跳转且未出现密码框则点击 `Use another account` 退回标准邮箱输入；
   - **分支 B（标准邮箱+密码登录）**：填入 `#email` -> 点击 `#continue` -> 动态轮询等待 `#password` 出现（期间若弹出邮箱后 hCaptcha 立即调用 `agent.wait_for_challenge()` 求解）-> 填入 `#password` -> 点击 `#sign-in`；
   - **提交后轮询（最多 35 秒）**：实时检测页面跳出 `/id/login`、拦截跳过 `"Protect Your Account"` (2FA 提醒弹窗，点击 `Maybe later` / `以后再说`)、并按需求解尾随 hCaptcha。
5. **获取周免列表 (`get_promotions`)**：
   - 直接请求公开接口 `https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=en-US`，筛选 `discountPercentage == 0` 的当前免费游戏，智能区分单品 (`/en-US/p/`) 与捆绑包 (`/en-US/bundles/`)。
6. **逐款游戏检查与领取 (`EpicGames.add_promotion_to_cart`)**：
   - 打开商品页 -> 关闭 OneTrust Cookie 横幅 -> 调用 `_handle_age_gate_and_takeover` 处理 18+ 门禁；
   - 定位主 CTA 按钮：若文本包含 `IN LIBRARY / OWNED / 已在库中 / 已拥有`，直接记入 `skipped_urls` 跳过；
   - 若为 `Get / 获取`，挂载 `AgentV` 并调用 `_click_and_check_modal` 点击；
   - **就地重登防御**：若点击后重定向至 `/id/login`，调用 `auth._login()`，重返商品页后**第一优先级检查按钮是否已变为 `In Library`**（若是则直接跳过，绝不进入结账避免超时与假阳性误报）；
   - 进入 `_handle_instant_checkout` 即时结账 -> 穿透定位并点击 `Place Order / Add to library / 下订单 / 添加到库` -> 处理前置/后置 hCaptcha -> 调用 `_verify_library_status` 终验商品页按钮是否真实变为 `In Library`。
7. **精准 Bark 推送 (`collect_epic_games`)**：
   - `claimed = [p for p in promotions if p.url not in skipped_urls]`；
   - 仅对本次真正新入库的游戏发送 Bark 通知，已在库或失败跳过的游戏绝不误报。

---

## 3. 历史踩坑与绝对红线（必读！禁止回退）

### 红线 1：严禁恢复 `ajaxGetOrderHistory` 接口
- **背景 (`v1.1.8`)**：旧版通过访问 `https://www.epicgames.com/account/v2/payment/ajaxGetOrderHistory` 拉取历史订单。如今该接口会强制触发 302 重定向至 `/id/login` 并**直接冲刷掉浏览器原本有效的登录 Session**，还会造成 30 秒超时。
- **规则**：`_sync_order_history` 与 `_check_orders` 必须保持为空/仅调用 `get_promotions()`，在库状态一律以商品详情页主按钮为准。

### 红线 2：严禁使用全页文本扫描判断 `Owned / In Library`
- **背景 (`v1.1.11` ~ `v1.1.13`)**：游戏详情页底部的推荐栏、DLC 列表或描述文本中经常包含 `"Owned"` 或 `"In Library"` 字样。若对全页或宽泛容器做文本扫描，会把未领取的新游戏误判为“已在库中”而直接漏领。
- **规则**：只允许检查主购买按钮 (`[data-testid='purchase-cta-button']` 等精确按钮文本) 或 `PurchaseCard` 侧边栏组件。

### 红线 3：`Welcome back` 账号卡片选择器严禁包含宽泛的 `div`
- **背景 (`v1.1.18` ~ `v1.1.19`)**：在 Epic `'Welcome back'` 界面，若选择器写成 `"button, div, a"` 并配合 `.filter(has_text=...)`，`.first` 会命中页面最外层的根 `<div>` 容器，导致点击落空在页面空白中心，随后退回 `Use another account` 输入邮箱，而 Epic 已知该邮箱在本地有记录又跳回 `Welcome back`，引发死循环与风控验证码。
- **规则**：只允许匹配可交互元素 `button, [role='button'], a, li, [tabindex='0']` 或使用 `page.get_by_text(account_pattern).first`。

### 红线 4：严禁无条件阻塞调用 `agent.wait_for_challenge()`
- **背景 (`v1.1.16` ~ `v1.1.18`)**：在未弹出验证码时盲目调用 `wait_for_challenge()` 会干等 30 秒甚至阻塞后续流程。
- **规则**：必须先通过遍历 `page.frames` 确认存在可见的 `hcaptcha.com` 且 `frame=challenge`（或 `frame=checkbox`），再唤醒 `agent.wait_for_challenge()`；且在结算等待循环中，**求解完验证码后必须重置 `start_time = time.time()`**，给结算按钮留足 60 秒渲染时间。

### 红线 5：18+ 成人分级游戏的双重门禁与会话脱落 (`v1.1.20`)
- **背景**：在 `/en-US/free-games` 主页顶栏显示已登录（前端缓存），但跳转进入 18+ 成人分级游戏（如《Sker Ritual》）时，后端触发高权限年龄核验，若商城子域 Token 失效或需强化认证，会弹出全屏遮罩 `<div data-rac class="... _takeover_...">` 显示 **"Sign in to confirm your age and view this content"**，同时底层按钮降级渲染为游客态的 `Get`。若直接点底层的 `Get`，会被全屏遮罩拦截指针事件（`intercepts pointer events`）死锁 30 秒崩溃。
- **规则**：
  1. 进入商品页后必须先执行 `_handle_age_gate_and_takeover(page, url)`；
  2. 若检测到 `"Sign in to confirm your age"` 遮罩，主动点击遮罩内的 `Sign In` 按钮恢复会话并重返商品页（使用 `not page.url.startswith(url)` 判断，防止 `redirectUrl=` 参数包含目标 URL 造成误判）；
  3. 若检测到常规分级警告遮罩，点击 `Continue / 继续 / View Product / View Game` 放行；
  4. `for url in urls:` 循环体内必须保持单游戏 `try...except` 隔离，单款游戏异常绝不能中断后续周免游戏的领取。

### 红线 6：`settings.py` 中的 Gemini 与 hCaptcha 猴子补丁不可删除
- `settings._apply_gemini_proxy_patch()` 解决了五大底层兼容性问题：
  1. **中转地址注入**：劫持 `genai.Client.__init__`，自动剥离末尾 `/v1` 并注入 `GEMINI_BASE_URL`；
  2. **多图上传 400 修复**：劫持 `AsyncFiles.upload` 与 `AsyncModels.generate_content`，用 `uuid4` 内存缓存将图片转为 `Part.from_bytes` 内联 Base64 发送，并移除 `media_resolution=HIGH` 写死属性；
  3. **多层嵌套 Frame 穿透**：Epic 结算窗的 hCaptcha 嵌套在 `#webPurchaseContainer` iframe 内部，支持最多 6 层递归与 `page.frames` 全遍历（兼容 `assets.hcaptcha.com`）；
  4. **`/checkcaptcha/` 空回包容错 (`v1.1.16` ~ `v1.1.17`)**：当响应体为空或校验后挑战 Frame 已消失时，构造 `CaptchaResponse(**{"pass": True, ...})`（注意 `pass` 是 Python 关键字，必须用字典解包触发 Pydantic alias）并立即判定通过；
  5. **空队列快速降级**：`_captcha_payload_queue` 为空时最多等待 2.0 秒即转入视觉截图识别，避免漏包时干等 30 秒。

### 红线 7：Playwright API 兼容性与脚本换行符
- 容器内 Playwright 版本的 `Locator.is_visible()` **不支持传入 `timeout` 参数**。等待元素可见必须先用 `with suppress(Exception): await locator.wait_for(state="visible", timeout=...)`，再调用无参的 `await locator.is_visible()`。
- `docker/docker-entrypoint.sh` 及 `.gitattributes` 已配置 `*.sh text eol=lf`，严禁将 `.sh` 脚本改为 `CRLF`。

---

## 4. 版本发布与验证标准流程 (SOP)

每次完成代码修改后，按以下顺序执行：

1. **语法编译检查**：
   ```powershell
   python -m py_compile app/deploy.py app/settings.py app/services/epic_authorization_service.py app/services/epic_games_service.py
   ```
2. **提交与打 Tag（触发 GitHub Actions 构建多架构镜像）**：
   ```powershell
   git commit -am "fix: 简明描述修改内容 (v1.1.xx)"
   git tag -a v1.1.xx -m "Release v1.1.xx: 简明描述"
   git push origin main
   git push origin v1.1.xx
   ```
3. **生产服务器更新验证**：
   等待 GitHub Actions 完成构建（约 3~5 分钟）后，在服务器执行：
   ```bash
   docker compose pull && docker compose up -d && docker compose logs -f
   ```
