# -*- coding: utf-8 -*-
"""
Epic Games Free Game Collection - Authorization Service

Handles the login process, 2FA bypass (if applicable), and session management
for the Epic Games account."""
import asyncio
import json
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
            # else:
            #     logger.debug(f"{r.request.method} {r.url} - {result_json}")

    async def _handle_right_account_validation(self):
        """
        以下验证仅会在登录成功后出现
        Returns:

        """
        await self.page.goto("https://www.epicgames.com/account/personal", wait_until="networkidle")

        btn_ids = ["#link-success", "#login-reminder-prompt-setup-tfa-skip", "#yes"]

        # 账号长期不登录可能需要跳过提示
        for _ in range(10):
            if not self._is_refresh_csrf_signal.empty() or not btn_ids:
                break
            await self.page.wait_for_timeout(500)
            for action in list(btn_ids):
                with suppress(Exception):
                    reminder_btn = self.page.locator(action)
                    if await reminder_btn.is_visible(timeout=1000):
                        await reminder_btn.click()
                        btn_ids.remove(action)

    async def _login(self) -> bool | None:
        agent = AgentV(page=self.page, agent_config=settings)
        logger.debug("Login with Email")

        try:
            # 如果当前页面已经在登录页且有 #email，无需重新加载
            email_input = self.page.locator("#email").first
            is_email_visible = False
            with suppress(Exception):
                is_email_visible = await email_input.is_visible()

            if not is_email_visible:
                point_url = "https://www.epicgames.com/account/personal?lang=en-US&productName=egs&sessionInvalidated=true"
                await self.page.goto(point_url, wait_until="domcontentloaded")
                email_input = self.page.locator("#email").first
                await email_input.wait_for(state="visible", timeout=20000)

            # 1. 使用电子邮件地址登录
            await email_input.clear()
            await email_input.type(settings.EPIC_EMAIL)

            # 2. 点击继续按钮
            await self.page.click("#continue")

            # 3. 关键：点击继续后，Epic 可能会立即弹出人机验证（验证码出现在输入密码之前）！
            try:
                logger.debug("Checking for CAPTCHA after email submit...")
                await agent.wait_for_challenge()
            except Exception as e:
                logger.debug(f"No CAPTCHA after email submit (or skipped): {e}")

            # 4. 输入密码 (等待密码输入框就绪)
            password_input = self.page.locator("#password")
            await password_input.wait_for(state="visible", timeout=20000)
            await password_input.clear()
            await password_input.type(settings.EPIC_PASSWORD.get_secret_value())

            # 5. 点击登录按钮，触发人机挑战值守监听器
            sign_in_btn = self.page.locator("#sign-in, button[type='submit']").first
            await sign_in_btn.wait_for(state="visible", timeout=15000)
            await sign_in_btn.click()

            # 6. 点击登录后，可能再次触发人机挑战
            try:
                await agent.wait_for_challenge()
            except Exception as e:
                logger.debug(f"No CAPTCHA after sign-in (or skipped): {e}")

            # 等待登录成功信号或页面离开登录页
            try:
                await asyncio.wait_for(self._is_login_success_signal.get(), timeout=30)
            except asyncio.TimeoutError:
                if "/id/login" not in self.page.url:
                    logger.debug("Redirected away from login page, inferring login success.")
                else:
                    raise TimeoutError("Login timed out and still on /id/login")

            logger.success("Login success")

            with suppress(Exception):
                await asyncio.wait_for(self._handle_right_account_validation(), timeout=30)
                logger.success("Right account validation success")
            return True
        except Exception as err:
            logger.warning(f"{err}")
            sr = SCREENSHOTS_DIR.joinpath("authorization")
            sr.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=sr.joinpath(f"login-{int(time.time())}.png"))
            return None

    async def invoke(self):
        self.page.on("response", self._on_response_anything)

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
