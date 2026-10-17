# -*- coding: utf-8 -*-
"""
Epic Games Free Game Collection - Core Service

Contains the main logic for interacting with the Epic Games Store, including
checkout automation and status detection."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from contextlib import suppress
from json import JSONDecodeError
import re
import time
from typing import List

import httpx
from hcaptcha_challenger.agent import AgentV
from loguru import logger
from playwright.async_api import Page
from playwright.async_api import expect, TimeoutError, FrameLocator
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from models import OrderItem, Order
from models import PromotionGame
from settings import settings, RUNTIME_DIR
from utils import send_bark_notification
try:
    from services.epic_authorization_service import EpicAuthorization
except ImportError:
    from app.services.epic_authorization_service import EpicAuthorization

URL_CLAIM = "https://store.epicgames.com/en-US/free-games"
URL_LOGIN = (
    f"https://www.epicgames.com/id/login?lang=en-US&noHostRedirect=true&redirectUrl={URL_CLAIM}"
)
URL_CART = "https://store.epicgames.com/en-US/cart"
URL_CART_SUCCESS = "https://store.epicgames.com/en-US/cart/success"


URL_PROMOTIONS = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
URL_PRODUCT_PAGE = "https://store.epicgames.com/en-US/p/"
URL_PRODUCT_BUNDLES = "https://store.epicgames.com/en-US/bundles/"


def get_promotions() -> List[PromotionGame]:
    """获取周免游戏数据"""
    def is_discount_game(prot: dict) -> bool | None:
        with suppress(KeyError, IndexError, TypeError):
            offers = prot["promotions"]["promotionalOffers"][0]["promotionalOffers"]
            for i, offer in enumerate(offers):
                if offer["discountSetting"]["discountPercentage"] == 0:
                    return True

    promotions: List[PromotionGame] = []

    try:
        resp = httpx.get(URL_PROMOTIONS, params={"locale": "en-US"}, timeout=10.0)
        data = resp.json()
    except Exception as err:
        logger.error("Failed to get promotions", err=err)
        return []

    with suppress(Exception):
        cache_key = RUNTIME_DIR.joinpath("promotions.json")
        cache_key.parent.mkdir(parents=True, exist_ok=True)
        cache_key.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # Get store promotion data and <this week free> games
    for e in data["data"]["Catalog"]["searchStore"]["elements"]:
        if not is_discount_game(e):
            continue

        # -----------------------------------------------------------
        # 🟢 智能 URL 识别逻辑
        # -----------------------------------------------------------
        is_bundle = False
        if e.get("offerType") == "BUNDLE":
            is_bundle = True
        
        # 补充检测：分类和标题
        if not is_bundle:
            for cat in e.get("categories", []):
                if "bundle" in cat.get("path", "").lower():
                    is_bundle = True
                    break
        if not is_bundle and "Collection" in e.get("title", ""):
             is_bundle = True

        base_url = URL_PRODUCT_BUNDLES if is_bundle else URL_PRODUCT_PAGE

        try:
            if e.get('offerMappings'):
                slug = e['offerMappings'][0]['pageSlug']
                e["url"] = f"{base_url.rstrip('/')}/{slug}"
            elif e.get("productSlug"):
                e["url"] = f"{base_url.rstrip('/')}/{e['productSlug']}"
            else:
                 e["url"] = f"{base_url.rstrip('/')}/{e.get('urlSlug', 'unknown')}"
        except (KeyError, IndexError):
            logger.info(f"Failed to get URL: {e}")
            continue

        logger.info(e["url"])
        promotions.append(PromotionGame(**e))

    return promotions


class EpicAgent:
    def __init__(self, page: Page):
        self.page = page
        self.epic_games = EpicGames(self.page)
        self._promotions: List[PromotionGame] = []
        self._ctx_cookies_is_available: bool = False
        self._orders: List[OrderItem] = []
        self._namespaces: List[str] = []
        self._cookies = None

    async def _sync_order_history(self):
        # 彻底废除此接口：ajaxGetOrderHistory 现已强制重定向到 /id/login，会冲刷有效 Session 并超时 30 秒
        self._orders = []

    async def _check_orders(self):
        # 移除已废弃且会导致登录态被清空的 ajaxGetOrderHistory 请求
        self._promotions = get_promotions()

    async def _should_ignore_task(self) -> bool:
        self._ctx_cookies_is_available = False
        await self.page.goto(URL_CLAIM, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2000)

        # 检查是否为未登录态 (存在明确的 Sign In 按钮)
        sign_in_btn = self.page.locator(
            "//header//button[contains(., 'Sign in') or contains(., 'Sign In') or contains(., '登录')] | "
            "//header//a[contains(., 'Sign in') or contains(., 'Sign In') or contains(., '登录')] | "
            "//header//span[contains(., 'Sign in') or contains(., 'Sign In') or contains(., '登录')] | "
            "a[role='button']:has-text('Sign In')"
        ).first
        with suppress(Exception):
            if await sign_in_btn.is_visible():
                logger.error("❌ 无法领取：检测到当前页面存在 'Sign in' 按钮，登录会话已失效")
                return False

        nav = self.page.locator("//egs-navigation").first
        status = None
        display_name = None
        with suppress(Exception):
            status = await nav.get_attribute("isloggedin")
            display_name = await nav.get_attribute("displayname")

        # 只有 status 为 true 且 displayname 存在且不为 'null' 时才是有效会话
        if status != "true" or not display_name or display_name.lower() == "null":
            logger.error(f"❌ 无法领取：登录会话无效 (status={status}, displayname={display_name})")
            return False

        self._ctx_cookies_is_available = True
        await self._check_orders()
        if not self._promotions:
            return True
        return False

    async def collect_epic_games(self):
        if await self._should_ignore_task():
            logger.success("All week-free games are already in the library")
            return

        if not self._ctx_cookies_is_available:
            logger.warning("Session cookies not available, attempting re-authorization...")
            auth = EpicAuthorization(self.page)
            if await auth.invoke():
                self._ctx_cookies_is_available = True
            else:
                logger.error("Re-authorization failed, aborting task.")
                return

        if not self._promotions:
            await self._check_orders()

        if not self._promotions:
            logger.success("All week-free games are already in the library")
            return

        for p in self._promotions:
            pj = json.dumps({"title": p.title, "url": p.url}, indent=2, ensure_ascii=False)
            logger.debug(f"Discover promotion \n{pj}")

        if self._promotions:
            try:
                claimed_games = await self.epic_games.collect_weekly_games(self._promotions)
                # 只对本次实际领取成功的游戏发送通知（已在库中的跳过）
                if claimed_games:
                    for p in claimed_games:
                        await send_bark_notification("Epic 游戏领取", f"《{p.title}》领取成功")
                else:
                    logger.info("本次没有新领取的游戏，跳过 Bark 推送")
            except Exception as e:
                logger.exception(e)
        
        logger.debug("All tasks in the workflow have been completed")


class EpicGames:
    def __init__(self, page: Page):
        self.page = page
        self._promotions: List[PromotionGame] = []

    @staticmethod
    async def _save_debug_screenshot(page: Page, name: str) -> None:
        """保存调试截图，使用可读的 UTC 时间戳命名。"""
        from settings import SCREENSHOTS_DIR
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOTS_DIR.joinpath(f"{name}_{ts}.png")
        with suppress(Exception):
            await page.screenshot(path=path, full_page=True)
            logger.debug(f"📸 Screenshot saved: {path.name}")

    @staticmethod
    def _cleanup_old_screenshots(max_age_days: int = 30) -> None:
        """删除超过 max_age_days 天的截图，防止磁盘持续积累。"""
        from settings import SCREENSHOTS_DIR
        if not SCREENSHOTS_DIR.exists():
            return
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
        removed = 0
        for f in SCREENSHOTS_DIR.glob("*.png"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                with suppress(Exception):
                    f.unlink()
                    removed += 1
        if removed:
            logger.debug(f"Cleaned up {removed} screenshot(s) older than {max_age_days} days")

    @staticmethod
    async def _agree_license(page: Page):
        logger.debug("Agree license")
        with suppress(TimeoutError):
            await page.click("//label[@for='agree']", timeout=4000)
            accept = page.locator("//button//span[text()='Accept']")
            if await accept.is_enabled():
                await accept.click()

    @staticmethod
    async def _find_checkout_button(page: Page, wpc: FrameLocator):
        btn_regex = re.compile(r"(Add to library|添加到库|Place Order|下订单)", re.I)

        # 1. 尝试在 wpc (购买主 iframe) 中查找
        try:
            btn = wpc.locator("button, [role='button']").filter(has_text=btn_regex).first
            if await btn.is_visible():
                return wpc, btn
        except Exception:
            pass

        # 2. 尝试在顶级 page DOM 中查找
        try:
            btn = page.locator("button, [role='button']").filter(has_text=btn_regex).first
            if await btn.is_visible():
                return page, btn
        except Exception:
            pass

        # 3. 遍历 page.frames 中所有 iframe (深度穿透，防止多层嵌套或域名变动)
        for f in page.frames:
            if f.is_detached():
                continue
            try:
                btn = f.locator("button, [role='button']").filter(has_text=btn_regex).first
                if await btn.is_visible():
                    return f, btn
            except Exception:
                pass

        # 4. CSS 选择器兜底
        css_candidates = [
            "button[class*='payment-confirm__btn']",
            "button[data-testid*='confirm-order']",
            "button[data-testid*='purchase-cta-button']",
            "button.btn-primary",
        ]
        for sel in css_candidates:
            with suppress(Exception):
                btn = wpc.locator(sel).first
                if await btn.is_visible():
                    return wpc, btn
            with suppress(Exception):
                btn = page.locator(sel).first
                if await btn.is_visible():
                    return page, btn

        return None, None

    @staticmethod
    async def _active_purchase_container(page: Page, agent: AgentV = None):
        logger.debug("Scanning for purchase iframe...")
        iframe_selector = "//iframe[contains(@id, 'webPurchaseContainer') or contains(@src, 'purchase') or contains(@src, 'checkout')]"
        wpc = page.frame_locator(iframe_selector).first

        logger.debug("Looking for purchase/checkout button or CAPTCHA...")
        start_time = time.time()
        max_wait = 60.0

        while time.time() - start_time < max_wait:
            # 1. 优先检查阻碍性弹窗 (如 Device not supported / 年龄提示) 并自动点击 Continue
            try:
                continue_dialog_btn = page.locator(
                    "div[role='dialog'] button, [aria-modal='true'] button, button"
                ).filter(has_text=re.compile(r"^\s*(Continue|继续)\s*$", re.I)).first
                with suppress(Exception):
                    await continue_dialog_btn.wait_for(state="visible", timeout=1500)
                if await continue_dialog_btn.is_visible():
                    logger.warning("⚠️ 检测到阻挡弹窗 (Device not supported)，点击 Continue...")
                    await continue_dialog_btn.click()
                    await page.wait_for_timeout(1000)
                    start_time = time.time()
            except Exception:
                pass

            # 2. 深度穿透检测屏幕是否弹出了人机验证码 (无论嵌套多少层 iframe)
            has_visible_challenge = False
            for f in page.frames:
                if not f.is_detached() and "hcaptcha.com" in f.url and "frame=challenge" in f.url:
                    try:
                        view = f.locator("//div[contains(@class, 'challenge-view')] | //div[contains(@class, 'task-image')] | body").first
                        if await view.is_visible():
                            has_visible_challenge = True
                            break
                    except Exception:
                        has_visible_challenge = True
                        break

            # 同样检测是否有独立的 checkbox 需要点击勾选
            if not has_visible_challenge:
                for f in page.frames:
                    if not f.is_detached() and "hcaptcha.com" in f.url and "frame=checkbox" in f.url:
                        with suppress(Exception):
                            cb = f.locator("//div[@id='checkbox'] | #checkbox").first
                            if await cb.is_visible():
                                logger.info("⚡ 检测到 hCaptcha 复选框，尝试点击勾选...")
                                await cb.click()
                                await page.wait_for_timeout(1000)
                                start_time = time.time()
                                break

            if has_visible_challenge and agent is not None:
                logger.info("⚡ 检测到屏幕弹出人机验证码 (frame=challenge)，正在调用 Gemini AI 求解...")
                try:
                    res = await agent.wait_for_challenge()
                    logger.info(f"✅ 人机验证求解返回: {res}，等待结算按钮就绪...")
                except Exception as ce:
                    logger.warning(f"人机验证求解过程异常: {ce}")
                await page.wait_for_timeout(3000)
                # 关键修复：人机验证耗时可能达数分钟，求解完成后必须重置 start_time，重新赋予完整的 60 秒等待结算按钮！
                start_time = time.time()
                continue

            # 3. 查找结算/添加入库按钮 (穿透主 iframe、顶级 DOM 以及所有子 frames)
            container, checkout_btn = await EpicGames._find_checkout_button(page, wpc)
            if checkout_btn:
                btn_name = ""
                with suppress(Exception):
                    btn_name = await checkout_btn.text_content()
                logger.success(f"✅ 成功定位到结算按钮: '{btn_name.strip()}'")
                return container, checkout_btn

            # 4. 检查是否有需要勾选的条款 (如欧盟撤回权声明、隐私条款)
            try:
                terms_checkbox = wpc.locator("input[type='checkbox']").first
                if await terms_checkbox.is_visible():
                    if not await terms_checkbox.is_checked():
                        await terms_checkbox.check(force=True)
            except Exception:
                pass

            await page.wait_for_timeout(1000)

        logger.warning("Primary buttons not found in iframe or page within timeout.")
        raise AssertionError("Could not find Place Order or Add to Library button in iframe or page")

    @staticmethod
    async def _uk_confirm_order(wpc: FrameLocator):
        logger.debug("UK confirm order")
        with suppress(TimeoutError):
            accept = wpc.locator("//button[contains(@class, 'payment-confirm__btn')]")
            if await accept.is_enabled(timeout=5000):
                await accept.click()
                return True

    async def _verify_library_status(self, page: Page) -> bool:
        """
        验证游戏是否真实成功入库：
        等待结算弹窗消失，检查游戏详情页的主按钮状态是否变为 In Library / 已在库中 / Owned。
        若未变化，尝试刷新页面再次校验。只有按钮确实为已拥有状态，才返回 True。
        """
        logger.debug("正在执行入库状态终检验...")
        # 等待结算 iframe 或弹窗关闭 (最多等 8 秒)
        with suppress(Exception):
            iframe_locator = page.locator("//iframe[contains(@id, 'webPurchaseContainer') or contains(@src, 'purchase')]").first
            await iframe_locator.wait_for(state="hidden", timeout=8000)

        for attempt in range(3):
            try:
                cta_btn = page.locator(
                    "button[data-testid='purchase-cta-button'], "
                    "[data-testid='purchase-cta-button'] button, "
                    "button, a[role='button']"
                ).filter(
                    has_text=re.compile(
                        r"^\s*(In Library|IN LIBRARY|Owned|OWNED|已在库中|已拥有|Get|GET|获取)\s*$",
                        re.I,
                    )
                ).first

                with suppress(Exception):
                    await cta_btn.wait_for(state="visible", timeout=3000)

                if await cta_btn.is_visible():
                    btn_text = (await cta_btn.text_content() or "").strip()
                    btn_text_upper = btn_text.upper()
                    if any(kw in btn_text_upper for kw in ["IN LIBRARY", "OWNED", "已在库中", "已拥有"]):
                        logger.success(f"🎉 入库校验成功：游戏详情页按钮已变为 '{btn_text}'")
                        await self._save_debug_screenshot(page, "success_in_library_confirmed")
                        return True
                    elif any(kw in btn_text_upper for kw in ["GET", "获取"]):
                        logger.warning(f"入库校验检测到按钮仍为 '{btn_text}' (尚未成功入库)")
            except Exception as e:
                logger.debug(f"按钮状态检测尝试 {attempt + 1}: {e}")

            if attempt == 1:
                # 第二次尝试时主动刷新页面，拉取最新状态
                with suppress(Exception):
                    logger.debug("刷新页面以拉取最新商品状态...")
                    await page.reload(wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)

            await page.wait_for_timeout(2000)

        logger.error("❌ 入库核验失败：未能确认游戏已进入库中")
        await self._save_debug_screenshot(page, "fail_not_in_library")
        return False

    async def _handle_instant_checkout(self, page: Page, agent: AgentV = None):
        logger.info("Triggering Instant Checkout Flow...")
        if agent is None:
            agent = AgentV(page=page, agent_config=settings)

        try:
            # 1. 查找结算容器与支付按钮（内含前置验证码自动求解）
            wpc, payment_btn = await self._active_purchase_container(page, agent=agent)
            btn_text = ""
            with suppress(Exception):
                btn_text = await payment_btn.text_content() or ""
            logger.debug(f"Clicking payment button: {btn_text.strip()}")

            # 点击前截图（记录弹窗就绪状态）
            await self._save_debug_screenshot(page, "before_click")

            await payment_btn.click(force=True)
            await page.wait_for_timeout(2000)

            # 2. 点击后检测是否触发了二阶段人机验证码 (轮询最多 5 秒)
            try:
                for _ in range(5):
                    has_post_challenge = any(
                        not f.is_detached() and "hcaptcha.com" in f.url and ("frame=challenge" in f.url or "frame=checkbox" in f.url)
                        for f in page.frames
                    )
                    if has_post_challenge:
                        logger.info("⚡ 点击结算后触发二阶段人机验证，正在调用 Gemini AI 求解...")
                        with suppress(Exception):
                            await agent.wait_for_challenge()
                        break
                    await page.wait_for_timeout(1000)
            except Exception as e:
                logger.debug(f"Post-click CAPTCHA check: {e}")

            # 处理完验证码后截图（记录中间状态）
            await self._save_debug_screenshot(page, "after_captcha")

            # 3. 检查 iframe 中是否有明确的订单感谢信息
            for _ in range(6):
                try:
                    iframe_body = page.frame_locator("//iframe[contains(@id, 'webPurchaseContainer') or contains(@src, 'purchase')]").first
                    iframe_text = await iframe_body.locator("body").text_content() or ""
                    if any(kw in iframe_text for kw in ["Thank you for your order", "Thanks for your order", "Thank You", "Thanks for buying"]):
                        logger.success("Instant Checkout: Confirmed via Order Confirmation in iframe!")
                        await self._save_debug_screenshot(page, "success_iframe_confirmed")
                        return True
                except Exception:
                    # iframe 可能已关闭
                    break
                await page.wait_for_timeout(1000)

        except Exception as err:
            logger.warning(f"Instant checkout process encountered exception: {err}")
            await self._save_debug_screenshot(page, "checkout_error")

        # 4. 终极入库实锤校验：核查详情页主按钮是否已变为 "In Library"
        return await self._verify_library_status(page)

    async def add_promotion_to_cart(self, page: Page, urls: List[str]) -> tuple[bool, set]:
        """返回 (has_pending_cart_items, skipped_urls)。
        skipped_urls 是确认已在库中或无效、本次被跳过的 URL 集合。
        """
        has_pending_cart_items = False
        skipped_urls: set = set()

        for url in urls:
            await page.goto(url, wait_until="load")
            await page.wait_for_timeout(2000)

            # 404 检测
            title = await page.title()
            if "404" in title or "Page Not Found" in title:
                logger.error(f"❌ Invalid URL (404 Page): {url}")
                await self._save_debug_screenshot(page, "skip_404")
                skipped_urls.add(url)
                continue

            # 处理 Cookie 弹窗（若存在则关闭，防止阻挡点击）
            try:
                cookie_btn = page.locator(
                    "#onetrust-accept-btn-handler, button:has-text('Accept All Cookies'), button:has-text('全部接受')"
                ).first
                with suppress(Exception):
                    await cookie_btn.wait_for(state="visible", timeout=2000)
                    if await cookie_btn.is_visible():
                        await cookie_btn.click()
            except Exception:
                pass

            # 处理年龄限制弹窗
            try:
                continue_btn = page.locator("//button//span[text()='Continue'] | button:has-text('Continue')").first
                with suppress(Exception):
                    await continue_btn.wait_for(state="visible", timeout=3000)
                    if await continue_btn.is_visible():
                        await continue_btn.click()
            except Exception:
                pass

            # 1. 尝试找到主按钮或库中状态
            # 综合定位器：优先 data-testid，再放宽至包含 Get / In Library 等关键词的按钮
            purchase_btn = (
                page.locator("button[data-testid='purchase-cta-button']")
                .or_(page.locator("[data-testid='purchase-cta-button'] button"))
                .or_(page.locator("[data-testid='purchase-cta-button']"))
                .or_(
                    page.locator("button, a[role='button']").filter(
                        has_text=re.compile(
                            r"^\s*(Get|GET|获取|In Library|IN LIBRARY|Owned|OWNED|已在库中|已拥有)\s*$",
                            re.I,
                        )
                    )
                )
                .first
            )

            try:
                with suppress(Exception):
                    await purchase_btn.wait_for(state="visible", timeout=12000)
                if not await purchase_btn.is_visible():
                    # 备选检查 1：购买卡片或侧边栏是否明确标注已拥有
                    library_badge = page.locator(
                        "aside, [data-component='PurchaseCard'], [data-testid='purchase-cta-button']"
                    ).filter(has_text=re.compile(r"(In Library|Owned|已在库中|已拥有)", re.I)).first
                    if await library_badge.count() > 0 and await library_badge.is_visible():
                        logger.success(f"Already in the library (Purchase Area Scan) - {url=}")
                        skipped_urls.add(url)
                        continue

                    # 备选检查 2：任意可见的 Get / 获取 按钮
                    alt_btn = page.locator("button, a[role='button']").filter(
                        has_text=re.compile(r"^\s*(Get|GET|获取)\s*$", re.I)
                    ).first
                    if await alt_btn.is_visible():
                        purchase_btn = alt_btn
                    else:
                        logger.warning(f"Could not find any purchase button - {url=}")
                        await self._save_debug_screenshot(page, "skip_no_button")
                        skipped_urls.add(url)
                        continue
            except Exception as e:
                logger.warning(f"Error during purchase button detection: {e}")
                skipped_urls.add(url)
                continue

            # 2. 获取按钮文字
            btn_text = await purchase_btn.text_content()
            if not btn_text:
                btn_text = ""
            btn_text_upper = btn_text.strip().upper()

            logger.debug(f"👉 Found Button: '{btn_text}'")

            # 3. 黑名单检查：只有这些情况绝对不能点
            if any(
                s in btn_text_upper
                for s in [
                    "IN LIBRARY",
                    "OWNED",
                    "UNAVAILABLE",
                    "COMING SOON",
                    "REQUIRES BASE GAME",
                    "BASE GAME",
                    "已在库中",
                    "已拥有",
                ]
            ):
                logger.success(f"Game status is '{btn_text}' - Skipping. - {url=}")
                skipped_urls.add(url)
                continue

            # 内部辅助函数：点击并检测拦截弹窗 (如 Device not supported)
            async def _click_and_check_modal(target):
                await target.click()
                await page.wait_for_timeout(1000)
                try:
                    continue_dialog_btn = (
                        page.locator("div[role='dialog'] button, [aria-modal='true'] button, button")
                        .filter(has_text=re.compile(r"^\s*(Continue|继续)\s*$", re.I))
                        .first
                    )
                    with suppress(Exception):
                        await continue_dialog_btn.wait_for(state="visible", timeout=3000)
                        if await continue_dialog_btn.is_visible():
                            logger.warning("⚠️ Detected modal (e.g., 'Device not supported'), clicking Continue.")
                            await continue_dialog_btn.click()
                            await page.wait_for_timeout(1000)
                except Exception as e:
                    logger.debug(f"Modal check error: {e}")

            # 4. 白名单检查 (Add to Cart 特殊处理)
            if "CART" in btn_text_upper:
                logger.debug(f"🛒 Logic: Add To Cart - {url=}")
                await _click_and_check_modal(purchase_btn)
                has_pending_cart_items = True
                continue

            # 5. 盲点领取逻辑 (Get / Free / Purchase / Buy Now)
            logger.debug(f"⚡️ Logic: Aggressive Click (Text: {btn_text}) - {url=}")

            # 提前挂载 AgentV 监听 response，确保捕获点击触发的 /getcaptcha/ 网络请求
            agent = AgentV(page=page, agent_config=settings)
            await _click_and_check_modal(purchase_btn)

            # 检测点击后是否被跳转到了登录页
            if "/id/login" in page.url:
                logger.warning(f"⚠️ 点击购买后被重定向到登录页面，尝试就地重新登录... ({url})")
                auth = EpicAuthorization(page)
                if await auth._login():
                    logger.success("登录成功，重新加载游戏页面并再次尝试领取...")
                    await page.goto(url, wait_until="load")
                    await page.wait_for_timeout(2000)
                    try:
                        retry_btn = page.locator("button, a[role='button']").filter(
                            has_text=re.compile(r"^\s*(Get|GET|获取)\s*$", re.I)
                        ).first
                        with suppress(Exception):
                            await retry_btn.wait_for(state="visible", timeout=5000)
                            if await retry_btn.is_visible():
                                agent = AgentV(page=page, agent_config=settings)
                                await _click_and_check_modal(retry_btn)
                    except Exception:
                        pass
                else:
                    logger.error(f"❌ 登录失败，无法领取 {url}")
                    skipped_urls.add(url)
                    continue

            # 转入即时结账流程
            success = await self._handle_instant_checkout(page, agent=agent)
            if not success:
                logger.info(f"Retrying instant checkout for {url}...")
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(2000)
                    retry_btn = page.locator("button, a[role='button']").filter(
                        has_text=re.compile(r"^\s*(Get|GET|获取)\s*$", re.I)
                    ).first
                    with suppress(Exception):
                        await retry_btn.wait_for(state="visible", timeout=5000)
                        if await retry_btn.is_visible():
                            retry_agent = AgentV(page=page, agent_config=settings)
                            await _click_and_check_modal(retry_btn)
                            success = await self._handle_instant_checkout(page, agent=retry_agent)
                except Exception as e:
                    logger.warning(f"Failed to retry checkout, skipping {url}. Error: {e}")
                    success = False

            if not success:
                skipped_urls.add(url)  # 领取失败，剔除出待通知列表
            # ------------------------------------------------------------

        return has_pending_cart_items, skipped_urls

    async def _empty_cart(self, page: Page, wait_rerender: int = 30) -> bool | None:
        has_paid_free = False
        try:
            cards = await page.query_selector_all("//div[@data-testid='offer-card-layout-wrapper']")
            for card in cards:
                is_free = await card.query_selector("//span[text()='Free']")
                if not is_free:
                    has_paid_free = True
                    wishlist_btn = await card.query_selector(
                        "//button//span[text()='Move to wishlist']"
                    )
                    await wishlist_btn.click()

            if has_paid_free and wait_rerender:
                wait_rerender -= 1
                await page.wait_for_timeout(2000)
                return await self._empty_cart(page, wait_rerender)
            return True
        except TimeoutError as err:
            logger.warning("Failed to empty shopping cart", err=err)
            return False

    async def _purchase_free_game(self, retry_count: int = 0, max_retries: int = 3):
        await self.page.goto(URL_CART, wait_until="domcontentloaded")
        logger.debug("Move ALL paid games from the shopping cart out")
        await self._empty_cart(self.page)

        agent = AgentV(page=self.page, agent_config=settings)
        await self.page.click("//button//span[text()='Check Out']")
        await self._agree_license(self.page)

        try:
            logger.debug("Move to webPurchaseContainer iframe")
            wpc, payment_btn = await self._active_purchase_container(self.page, agent=agent)
            logger.debug("Click payment button")
            await self._uk_confirm_order(wpc)
            if payment_btn:
                with suppress(Exception):
                    await payment_btn.click(force=True)
            await agent.wait_for_challenge()
        except Exception as err:
            if retry_count >= max_retries:
                logger.error(f"Cart checkout failed after {max_retries} retries: {err}")
                return
            logger.warning(f"Failed to solve captcha (attempt {retry_count + 1}/{max_retries}) - {err}")
            await self.page.reload()
            return await self._purchase_free_game(retry_count + 1, max_retries)

    @retry(retry=retry_if_exception_type(TimeoutError), stop=stop_after_attempt(2), reraise=True)
    async def collect_weekly_games(self, promotions: List[PromotionGame]) -> List[PromotionGame]:
        """执行领取流程，返回本次实际领取成功的游戏列表。"""
        # 每次任务开始前清理超过 30 天的旧截图
        self._cleanup_old_screenshots(max_age_days=30)
        urls = [p.url for p in promotions]
        has_cart_items, skipped_urls = await self.add_promotion_to_cart(self.page, urls)

        if has_cart_items:
            await self._purchase_free_game()
            try:
                await self.page.wait_for_url(URL_CART_SUCCESS)
                logger.success("🎉 Successfully collected cart games")
            except TimeoutError:
                logger.warning("Failed to collect cart games")
                # 购物车结算失败时，所有在购物车里的游戏都没有领取成功
                return []
        # 计算本次真正成功的游戏与跳过的游戏
        claimed = [p for p in promotions if p.url not in skipped_urls]
        skipped = [p for p in promotions if p.url in skipped_urls]

        if claimed:
            titles = "、".join([f"《{p.title}》" for p in claimed])
            logger.success(f"🎉 本次成功领取 {len(claimed)} 款游戏: {titles}")
        elif skipped:
            titles = "、".join([f"《{p.title}》" for p in skipped])
            logger.info(f"ℹ️ 本次未领取新游戏，以下游戏已在库中或被跳过: {titles}")
        else:
            logger.info("ℹ️ 没有待领取的游戏")

        return claimed
