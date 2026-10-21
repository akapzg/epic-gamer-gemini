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

        # 账号长期不登录可能需要跳过提示
        for _ in range(10):
            if not self._is_refresh_csrf_signal.empty() or not btn_ids:
                break
            await self.page.wait_for_timeout(500)
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
            username = settings.EPIC_EMAIL.split("@")[0].strip()
            for _ in range(30):
                if "/id/login" not in self.page.url:
                    break
                if await self.page.locator("#password").is_visible():
                    break
                if await self.page.locator("#email").is_visible():
                    break
                welcome_loc = self.page.locator("text=Welcome back, text=欢迎回来").first
                if await welcome_loc.is_visible():
                    break
                use_another_btn = self.page.locator("button, a, div[role='button']").filter(
                    has_text=re.compile(r"Use another account|使用其他(账号|账户|帐户)", re.I)
                ).first
                if await use_another_btn.is_visible():
                    break
                await self.page.wait_for_timeout(500)

            # 3. 检测是否处于 "Welcome back" (已记住账号选择) 界面
            welcome_loc = self.page.locator("text=Welcome back, text=欢迎回来").first
            use_another_btn = self.page.locator("button, a, div[role='button'], [tabindex='0']").filter(
                has_text=re.compile(r"Use another account|使用其他(账号|账户|帐户)", re.I)
            ).first

            is_welcome_back = False
            with suppress(Exception):
                is_welcome_back = await welcome_loc.is_visible() or await use_another_btn.is_visible()

            if is_welcome_back:
                logger.info("检测到 Epic 'Welcome back' (记住账号切换) 界面...")

                # 寻找匹配当前用户名或邮箱域名的账号卡片
                account_card = self.page.locator("button, div[role='button'], a, li, [tabindex='0']").filter(
                    has_text=re.compile(re.escape(username), re.I)
                ).first

                card_found = False
                with suppress(Exception):
                    card_found = await account_card.is_visible()

                if not card_found:
                    with suppress(Exception):
                        account_card = self.page.get_by_text(username, exact=False).first
                        card_found = await account_card.is_visible()

                if card_found:
                    logger.info(f"👉 发现匹配账号卡片 '{username}'，点击快速登录...")
                    with suppress(Exception):
                        await account_card.click()
                    await self.page.wait_for_timeout(2000)

                    # 检查是否可能出现验证码
                    with suppress(Exception):
                        await agent.wait_for_challenge()

                    # 检查是否点击后直接完成会话恢复并跳转
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
                        else:
                            fallback_btn = self.page.get_by_text(
                                re.compile(r"Use another account|使用其他(账号|账户|帐户)", re.I)
                            ).first
                            if await fallback_btn.is_visible():
                                await fallback_btn.click()
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
                await email_input.clear()
                await email_input.type(settings.EPIC_EMAIL)

                # 点击继续按钮
                continue_btn = self.page.locator("#continue, button[type='submit']").first
                await continue_btn.click()

                # 关键：点击继续后，Epic 可能会立即弹出人机验证（验证码出现在输入密码之前）！
                try:
                    logger.debug("Checking for CAPTCHA after email submit...")
                    await agent.wait_for_challenge()
                except Exception as e:
                    logger.debug(f"No CAPTCHA after email submit (or skipped): {e}")

            # 5. 输入密码
            logger.info("输入账号密码...")
            password_input = self.page.locator("#password")
            await password_input.wait_for(state="visible", timeout=20000)
            await password_input.clear()
            await password_input.type(settings.EPIC_PASSWORD.get_secret_value())

            # 6. 点击登录提交按钮，触发人机挑战值守监听器
            sign_in_btn = self.page.locator("#sign-in, button[type='submit']").first
            await sign_in_btn.wait_for(state="visible", timeout=15000)
            await sign_in_btn.click()

            # 7. 点击登录后，可能再次触发人机挑战
            try:
                logger.debug("Checking for CAPTCHA after sign-in...")
                await agent.wait_for_challenge()
            except Exception as e:
                logger.debug(f"No CAPTCHA after sign-in (or skipped): {e}")

            # 8. 等待登录成功信号或页面离开登录页
            start_wait = time.time()
            while time.time() - start_wait < 35:
                if not self._is_login_success_signal.empty():
                    logger.debug("Received login success signal.")
                    break
                if "/id/login" not in self.page.url:
                    logger.debug(f"Redirected away from login page to {self.page.url}, inferring login success.")
                    break
                await self.page.wait_for_timeout(1000)
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
        logger.debug("正在验证 Epic Games 账号会话真实有效性...")
        for attempt in range(3):
            # 访问受鉴权保护的个人中心检验会话真实有效性；若会话过期，Epic 会立即 302 重定向到 /id/login
            try:
                await self.page.goto("https://www.epicgames.com/account/personal", wait_until="domcontentloaded")
                await self.page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"导航至个人中心重试 ({attempt + 1}/3): {e}")

            # 若被重定向到 /id/login，说明会话已过期，执行登录
            if "/id/login" in self.page.url:
                logger.info("检测到账号会话已失效 (被重定向至登录页)，启动登录流程...")
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
