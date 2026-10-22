# -*- coding: utf-8 -*-
"""
Epic Games Free Game Collection - Authorization Service

Handles the login process, 2FA bypass (if applicable), and session management
for the Epic Games account."""
import asyncio
import json
import re
import time
from contextlib import suppress

from hcaptcha_challenger.agent import AgentV
from loguru import logger
from playwright.async_api import expect, Page, Response

from settings import SCREENSHOTS_DIR, settings

URL_CLAIM = "https://store.epicgames.com/en-US/free-games"


class EpicAuthorization:

    def __init__(self, page: Page):
        self.page = page

        self._is_login_success_signal = asyncio.Queue()
        self._is_refresh_csrf_signal = asyncio.Queue()

        # 始终挂载响应监听器，确保无论通过 invoke() 还是直接调用 _login() 都能捕获鉴权信号
        with suppress(Exception):
            self.page.on("response", self._on_response_anything)

    async def _on_response_anything(self, r: Response):
        if r.request.method != "POST" or "talon" in r.url:
            return

        with suppress(Exception):
            result = await r.json()
            result_json = json.dumps(result, indent=2, ensure_ascii=False)

            if "/id/api/login" in r.url and result.get("errorCode"):
                logger.error(f"{r.request.method} {r.url} - {result_json}")
            elif "/id/api/analytics" in r.url and result.get("accountId"):
                self._is_login_success_signal.put_nowait(result)
            elif "/account/v2/refresh-csrf" in r.url and result.get("success", False) is True:
                self._is_refresh_csrf_signal.put_nowait(result)

    async def _handle_right_account_validation(self):
        """
        以下验证仅会在登录成功后出现
        """
        try:
            await self.page.goto("https://www.epicgames.com/account/personal", wait_until="domcontentloaded")
            await self.page.wait_for_timeout(2000)
        except Exception:
            pass

        btn_ids = ["#link-success", "#login-reminder-prompt-setup-tfa-skip", "#yes"]

        # 账号长期不登录可能需要跳过提示或处理 2FA 弹窗
        for _ in range(10):
            if not self._is_refresh_csrf_signal.empty() or not btn_ids:
                break
            await self.page.wait_for_timeout(500)

            # 额外检查并关闭 "Protect Your Account" 弹窗
            with suppress(Exception):
                maybe_later = self.page.locator("button, a").filter(
                    has_text=re.compile(r"Maybe later|以后再说|稍后", re.I)
                ).first
                if await maybe_later.is_visible():
                    await maybe_later.click()

            for action in list(btn_ids):
                with suppress(Exception):
                    reminder_btn = self.page.locator(action)
                    await reminder_btn.wait_for(state="visible", timeout=1000)
                    if await reminder_btn.is_visible():
                        await reminder_btn.click()
                        btn_ids.remove(action)

    async def _login(self) -> bool | None:
        agent = AgentV(page=self.page, agent_config=settings)
        logger.debug("Starting Epic Games login flow...")

        try:
            # 1. 确保在登录页面（如果在个人中心等页面，等待或跳转至登录页）
            if "/id/login" not in self.page.url:
                point_url = "https://www.epicgames.com/account/personal?lang=en-US&productName=egs&sessionInvalidated=true"
                logger.debug(f"正在导航至登录接入点: {point_url}")
                await self.page.goto(point_url, wait_until="domcontentloaded")
                await self.page.wait_for_timeout(2000)

            # 2. 等待登录页关键交互元素之一渲染就绪（最多等待 15 秒）
            email_prefix = settings.EPIC_EMAIL.split("@")[0].strip()
            email_domain = settings.EPIC_EMAIL.split("@")[-1].strip()

            for _ in range(30):
                if "/id/login" not in self.page.url:
                    break
                if await self.page.locator("#password").is_visible():
                    break
                if await self.page.locator("#email").is_visible():
                    break
                welcome_loc = self.page.get_by_text(re.compile(r"Welcome back|欢迎回来", re.I)).first
                if await welcome_loc.is_visible():
                    break
                use_another_btn = self.page.get_by_text(
                    re.compile(r"Use another account|使用其他(账号|账户|帐户)", re.I)
                ).first
                if await use_another_btn.is_visible():
                    break
                await self.page.wait_for_timeout(500)

            # 3. 检测是否处于 "Welcome back" (已记住账号选择) 界面
            welcome_loc = self.page.get_by_text(re.compile(r"Welcome back|欢迎回来", re.I)).first
            use_another_btn = self.page.get_by_text(
                re.compile(r"Use another account|使用其他(账号|账户|帐户)", re.I)
            ).first

            is_welcome_back = False
            with suppress(Exception):
                is_welcome_back = await welcome_loc.is_visible() or await use_another_btn.is_visible()

            if is_welcome_back:
                logger.info("检测到 Epic 'Welcome back' (记住账号切换) 界面...")

                # 寻找匹配当前用户名或邮箱域名的账号卡片
                account_pattern = re.compile(
                    rf"{re.escape(email_prefix)}|@{re.escape(email_domain)}", re.I
                )
                # 仅匹配交互容器（button, a, li, role='button'），绝对不包含宽泛的 div，防止点中外层大容器点空
                account_card = self.page.locator("button, [role='button'], a, li, [tabindex='0']").filter(
                    has_text=account_pattern
                ).first

                card_found = False
                with suppress(Exception):
                    card_found = await account_card.is_visible()

                if not card_found:
                    # 直接定位包含用户名或邮箱的文字节点，点击文字会自动冒泡触发整个卡片的点击事件
                    with suppress(Exception):
                        account_card = self.page.get_by_text(account_pattern).first
                        card_found = await account_card.is_visible()

                if card_found:
                    logger.info(f"👉 发现匹配账号卡片 ({email_prefix})，精准点击快速登录...")
                    with suppress(Exception):
                        await account_card.click()

                    # 动态轮询等待点击后的响应（最多 8 秒）：成功跳转、出现密码框、或触发人机验证
                    start_click_wait = time.time()
                    while time.time() - start_click_wait < 8:
                        if "/id/login" not in self.page.url:
                            break
                        if await self.page.locator("#password").is_visible():
                            break
                        with suppress(Exception):
                            await asyncio.wait_for(agent.wait_for_challenge(), timeout=1.0)
                        await self.page.wait_for_timeout(500)

                    # 检查是否点击后直接完成会话恢复并跳转离开登录页
                    if "/id/login" not in self.page.url:
                        logger.success("✅ 点击记住账号后直接完成登录/会话刷新！")
                        with suppress(Exception):
                            await asyncio.wait_for(self._handle_right_account_validation(), timeout=30)
                        return True

                # 如果点击卡片后未直接跳转，且密码框未显示，则点击 "Use another account" 切回标准邮箱登录
                pwd_visible = False
                with suppress(Exception):
                    pwd_visible = await self.page.locator("#password").is_visible()

                if not pwd_visible:
                    logger.info("未进入密码页，尝试点击 'Use another account' 切换至标准邮箱输入...")
                    with suppress(Exception):
                        if await use_another_btn.is_visible():
                            await use_another_btn.click()
                        await self.page.wait_for_timeout(1000)

            # 4. 判断是否需要输入邮箱，或者已经直接处于密码输入阶段
            is_pwd_ready = False
            with suppress(Exception):
                is_pwd_ready = await self.page.locator("#password").is_visible()

            if is_pwd_ready:
                logger.info("👉 密码输入框已就绪，跳过邮箱输入阶段...")
            else:
                logger.info(f"输入登录邮箱: {settings.EPIC_EMAIL}")
                email_input = self.page.locator("#email").first
                await email_input.wait_for(state="visible", timeout=15000)
                await email_input.fill(settings.EPIC_EMAIL)

                # 点击继续按钮
                continue_btn = self.page.locator("#continue, button[type='submit']").first
                await continue_btn.click()

                # 点击继续后，轮询等待密码输入框出现或人机验证码弹出（避免盲等 30 秒超时）
                logger.debug("等待密码输入框或人机验证...")
                start_email_wait = time.time()
                while time.time() - start_email_wait < 20:
                    if await self.page.locator("#password").is_visible():
                        logger.debug("密码输入框已就绪，无需人机验证")
                        break
                    with suppress(Exception):
                        await asyncio.wait_for(agent.wait_for_challenge(), timeout=2.0)
                    await self.page.wait_for_timeout(500)

            # 5. 输入密码
            logger.info("输入账号密码...")
            password_input = self.page.locator("#password")
            await password_input.wait_for(state="visible", timeout=20000)
            await password_input.fill(settings.EPIC_PASSWORD.get_secret_value())

            # 6. 点击登录提交按钮，触发人机挑战值守监听器
            sign_in_btn = self.page.locator("#sign-in, button[type='submit']").first
            await sign_in_btn.wait_for(state="visible", timeout=15000)
            await sign_in_btn.click()

            # 7. 等待登录成功信号、URL 跳转、人机验证或 2FA 提示弹窗（避免盲等 30 秒超时）
            logger.debug("等待登录跳转、人机验证或 2FA 提示...")
            start_wait = time.time()
            while time.time() - start_wait < 35:
                # 检查并自动跳过 "Protect Your Account" (2FA 提示) 弹窗
                maybe_later_btn = self.page.locator("button, a").filter(
                    has_text=re.compile(r"Maybe later|以后再说|稍后", re.I)
                ).first
                with suppress(Exception):
                    if await maybe_later_btn.is_visible():
                        logger.info("👉 检测到 'Protect Your Account' (2FA 提示) 弹窗，点击 'Maybe later' 跳过...")
                        await maybe_later_btn.click()
                        await self.page.wait_for_timeout(1000)

                if not self._is_login_success_signal.empty():
                    logger.debug("Received login success signal.")
                    break
                if "/id/login" not in self.page.url:
                    logger.debug(f"Redirected away from login page to {self.page.url}, inferring login success.")
                    break
                with suppress(Exception):
                    await asyncio.wait_for(agent.wait_for_challenge(), timeout=2.0)
                await self.page.wait_for_timeout(500)
            else:
                if "/id/login" in self.page.url:
                    raise TimeoutError("Login timed out and still on /id/login")

            logger.success("Login success")

            with suppress(Exception):
                await asyncio.wait_for(self._handle_right_account_validation(), timeout=30)
                logger.success("Right account validation success")
            return True
        except Exception as err:
            logger.warning(f"登录过程异常: {err}")
            sr = SCREENSHOTS_DIR.joinpath("authorization")
            sr.mkdir(parents=True, exist_ok=True)
            with suppress(Exception):
                await self.page.screenshot(path=sr.joinpath(f"login-{int(time.time())}.png"))
            return None

    async def invoke(self):
        logger.debug("正在严格验证 Epic Games 账号会话真实有效性...")
        for attempt in range(3):
            # 访问受鉴权保护的个人中心检验会话真实有效性；若会话过期，Epic 会在 1-4 秒内 302/客户端重定向到 /id/login
            try:
                await self.page.goto("https://www.epicgames.com/account/personal", wait_until="domcontentloaded")
            except Exception as e:
                logger.warning(f"导航至个人中心重试 ({attempt + 1}/3): {e}")

            # 给予页面最多 6 秒的 SPA 重定向缓冲，同时持续检测是否真实呈现已登录用户数据
            is_valid_session = False
            for _ in range(12):
                if "/id/login" in self.page.url:
                    break
                # 检测个人中心页面是否渲染出了个人信息（账号ID/邮箱/显示名称）
                with suppress(Exception):
                    email_field = self.page.locator("input[name='email'], input[name='displayName'], [data-component='AccountSettings']").first
                    if await email_field.is_visible():
                        is_valid_session = True
                        break
                    # 或者检测页面中是否出现了登录邮箱文本
                    if await self.page.get_by_text(settings.EPIC_EMAIL, exact=False).first.is_visible():
                        is_valid_session = True
                        break
                # 检测导航条登录状态
                with suppress(Exception):
                    nav = self.page.locator("//egs-navigation").first
                    status = await nav.get_attribute("isloggedin")
                    display_name = await nav.get_attribute("displayname")
                    if status == "true" and display_name and display_name.lower() != "null":
                        is_valid_session = True
                        break
                await self.page.wait_for_timeout(500)

            # 若被重定向到 /id/login 或未检测到任何可信的已登录用户身份，说明会话已失效，执行登录
            if "/id/login" in self.page.url or not is_valid_session:
                logger.info("检测到账号会话已失效 (未认证或被重定向至登录页)，启动登录流程...")
                if await self._login():
                    logger.success("登录成功，会话已恢复有效")
                    return True
                else:
                    logger.warning(f"登录尝试 {attempt + 1} 失败，准备重试...")
                    continue
            else:
                logger.success("Epic Games 账号会话真实有效，无需重新登录")
                return True

        return False
