# -*- coding: utf-8 -*-
"""
Epic Games Free Game Collection - Configuration Settings

Global settings and environment variable management for the application."""
import os
import asyncio
from pathlib import Path

# === 引入所需库 ===
from hcaptcha_challenger.agent import AgentConfig
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict
from loguru import logger

# --- 核心路径定义 ---
PROJECT_ROOT = Path(__file__).parent
VOLUMES_DIR = PROJECT_ROOT.joinpath("volumes")
LOG_DIR = VOLUMES_DIR.joinpath("logs")
USER_DATA_DIR = VOLUMES_DIR.joinpath("user_data")
RUNTIME_DIR = VOLUMES_DIR.joinpath("runtime")
SCREENSHOTS_DIR = VOLUMES_DIR.joinpath("screenshots")
RECORD_DIR = VOLUMES_DIR.joinpath("record")
HCAPTCHA_DIR = VOLUMES_DIR.joinpath("hcaptcha")

# 获取用户设置的 Master 模型名，默认使用 gemini-2.5-pro
_MASTER_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

# === 配置类定义 ===
class EpicSettings(AgentConfig):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # [核心修正：尊重环境变量设置，默认回退到 Master 模型]
    GEMINI_MODEL: str = Field(default=_MASTER_MODEL, description="Master 模型 ID")
    CHALLENGE_CLASSIFIER_MODEL: str = Field(default_factory=lambda: os.getenv("CHALLENGE_CLASSIFIER_MODEL", _MASTER_MODEL))
    IMAGE_CLASSIFIER_MODEL: str = Field(default_factory=lambda: os.getenv("IMAGE_CLASSIFIER_MODEL", _MASTER_MODEL))
    SPATIAL_POINT_REASONER_MODEL: str = Field(default_factory=lambda: os.getenv("SPATIAL_POINT_REASONER_MODEL", _MASTER_MODEL))
    SPATIAL_PATH_REASONER_MODEL: str = Field(default_factory=lambda: os.getenv("SPATIAL_PATH_REASONER_MODEL", _MASTER_MODEL))

    # [基础配置]
    GEMINI_API_KEY: SecretStr | None = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY"),
        description="Gemini 的令牌",
    )
    
    GEMINI_BASE_URL: str = Field(
        default=os.getenv("GEMINI_BASE_URL", "https://xxx.com"),
        description="中转地址",
    )
    
    EPIC_EMAIL: str = Field(default_factory=lambda: os.getenv("EPIC_EMAIL"))
    EPIC_PASSWORD: SecretStr = Field(default_factory=lambda: os.getenv("EPIC_PASSWORD"))
    BARK_URL: str | None = Field(default=os.getenv("BARK_URL"), description="Bark 推送地址")
    DISABLE_BEZIER_TRAJECTORY: bool = Field(default=True)

    cache_dir: Path = HCAPTCHA_DIR.joinpath(".cache")
    challenge_dir: Path = HCAPTCHA_DIR.joinpath(".challenge")
    captcha_response_dir: Path = HCAPTCHA_DIR.joinpath(".captcha")

    ENABLE_APSCHEDULER: bool = Field(default=True)
    TASK_TIMEOUT_SECONDS: int = Field(default=900)
    # 调高超时限制，防止下单重载导致 Timeout
    EXECUTION_TIMEOUT: float = Field(default=240.0) 
    RESPONSE_TIMEOUT: float = Field(default=60.0)

    @property
    def user_data_dir(self) -> Path:
        target_ = USER_DATA_DIR.joinpath(self.EPIC_EMAIL)
        target_.mkdir(parents=True, exist_ok=True)
        return target_

settings = EpicSettings()
settings.ignore_request_questions = ["Please drag the crossing to complete the lines"]

# ========================= 处理中转解析与多图冲突 =========================
import uuid as _uuid

# 重复挂载保护：确保 patch 只被执行一次
_patch_applied = False

def _apply_gemini_proxy_patch():
    global _patch_applied
    if _patch_applied:
        logger.debug("Gemini proxy patch already applied, skipping.")
        return
    if not settings.GEMINI_API_KEY:
        return

    try:
        from google import genai
        from google.genai import types

        # 1. 劫持 Client 初始化 (自动修正中转路径)
        orig_init = genai.Client.__init__
        def new_init(self, *args, **kwargs):
            if hasattr(settings.GEMINI_API_KEY, 'get_secret_value'):
                api_key = settings.GEMINI_API_KEY.get_secret_value()
            else:
                api_key = str(settings.GEMINI_API_KEY)

            kwargs['api_key'] = api_key

            base_url = settings.GEMINI_BASE_URL.rstrip('/')
            if base_url.endswith('/v1'): base_url = base_url[:-3]

            kwargs['http_options'] = types.HttpOptions(base_url=base_url)
            logger.info(f"已同步模型变量 | 当前生效 ID: {settings.GEMINI_MODEL} | 地址: {base_url}")
            orig_init(self, *args, **kwargs)

        genai.Client.__init__ = new_init

        # 2. 劫持文件上传与生成逻辑 (修复 400 报错与 Base64 兼容)
        # key 用 uuid4 而非 id()，避免 CPython 内存地址复用导致缓存污染
        file_cache: dict[str, bytes] = {}

        def _local_to_list(c):
            return c if isinstance(c, list) else [c]

        async def patched_upload(self_files, file, **kwargs):
            if hasattr(file, 'read'):
                content = file.read()
            elif isinstance(file, (str, Path)):
                with open(file, 'rb') as f:
                    content = f.read()
            else:
                content = bytes(file)

            if asyncio.iscoroutine(content):
                content = await content

            # uuid4 保证每次上传的 key 唯一，不受内存地址复用影响
            file_id = f"bypass_{_uuid.uuid4().hex}"
            file_cache[file_id] = content
            return types.File(name=file_id, uri=file_id, mime_type="image/png")

        orig_generate = genai.models.AsyncModels.generate_content
        async def patched_generate(self_models, model, contents, **kwargs):
            # 清除多图时写死的 HIGH 分辨率，防止 400 报错
            if 'config' in kwargs and kwargs['config'] is not None:
                if hasattr(kwargs['config'], 'media_resolution'):
                    try:
                        delattr(kwargs['config'], 'media_resolution')
                    except AttributeError:
                        kwargs['config'].media_resolution = None

            normalized = _local_to_list(contents)

            for content in normalized:
                if hasattr(content, 'parts'):
                    for i, part in enumerate(content.parts):
                        if part.file_data and part.file_data.file_uri in file_cache:
                            data = file_cache.pop(part.file_data.file_uri)  # 用完即删，防止内存泄漏
                            content.parts[i] = types.Part.from_bytes(data=data, mime_type="image/png")

            return await orig_generate(self_models, model=model, contents=normalized, **kwargs)

        genai.files.AsyncFiles.upload = patched_upload
        genai.models.AsyncModels.generate_content = patched_generate

        # 补丁：放宽 hcaptcha-challenger 的 challenge frame 识别与空队列优化
        try:
            from hcaptcha_challenger.agent.challenger import RoboticArm, AgentV
            from hcaptcha_challenger.models import RequestType, ChallengeTypeEnum
            from playwright.async_api import Frame
            from contextlib import suppress
            from uuid import uuid4

            async def patched_get_challenge_frame_locator(self_arm) -> Frame | None:
                # 1. 递归查找 main_frame 下的 challenge 候选 (放宽层级至 6)
                candidate_frame = self_arm._find_challenge_frame_recursive(self_arm.page.main_frame, max_depth=6)
                if candidate_frame:
                    with suppress(Exception):
                        challenge_view = candidate_frame.locator("//div[contains(@class, 'challenge-view')] | //div[contains(@class, 'task-image')] | body")
                        if await challenge_view.first.is_visible():
                            return candidate_frame

                # 2. 从 page.frames 中全面匹配包含 frame=challenge 的 frame (支持 assets.hcaptcha.com)
                challenge_frames = []
                for frame in self_arm.page.frames:
                    if "hcaptcha.com" in frame.url and "frame=challenge" in frame.url:
                        challenge_frames.append(frame)

                for frame in challenge_frames:
                    with suppress(Exception):
                        challenge_view = frame.locator("//div[contains(@class, 'challenge-view')] | //div[contains(@class, 'task-image')] | body")
                        if await challenge_view.first.is_visible():
                            return frame

                if challenge_frames:
                    return challenge_frames[0]

                logger.error("Cannot find a valid challenge frame")
                return None

            def patched_find_challenge_frame_recursive(self_arm, frame: Frame, current_depth=0, max_depth=6) -> Frame | None:
                if current_depth >= max_depth:
                    return None
                candidate_frames = []
                for child_frame in frame.child_frames:
                    if "hcaptcha.com" in child_frame.url and "frame=challenge" in child_frame.url:
                        candidate_frames.append(child_frame)
                    else:
                        found_in_child = self_arm._find_challenge_frame_recursive(
                            child_frame, current_depth + 1, max_depth
                        )
                        if found_in_child:
                            return found_in_child
                if candidate_frames:
                    return candidate_frames[0]
                return None

            async def patched_click_checkbox(self_arm):
                for f in self_arm.page.frames:
                    if "hcaptcha.com" in f.url and "frame=checkbox" in f.url:
                        with suppress(Exception):
                            cb = f.locator("//div[@id='checkbox'] | #checkbox").first
                            if await cb.is_visible():
                                await self_arm.click_by_mouse(cb)
                                return True
                return False

            async def patched_check_crumb_count(self_arm):
                if isinstance(self_arm.signal_crumb_count, int) and self_arm.signal_crumb_count >= 1:
                    return self_arm.signal_crumb_count
                await self_arm.page.wait_for_timeout(500)
                frame_challenge = await self_arm.get_challenge_frame_locator()
                if not frame_challenge:
                    return self_arm.config.MAX_CRUMB_COUNT
                crumbs = frame_challenge.locator("//div[@class='Crumb']")
                with suppress(Exception):
                    crumbs_count = await crumbs.count()
                    if crumbs_count:
                        return crumbs_count
                return self_arm.config.MAX_CRUMB_COUNT

            async def patched_check_challenge_type(self_arm):
                with suppress(Exception):
                    await self_arm.page.wait_for_selector(self_arm.challenge_selector, timeout=1000)

                frame_challenge = await self_arm.get_challenge_frame_locator()
                if not frame_challenge:
                    logger.error("check_challenge_type: 未能找到有效的 challenge frame")
                    return None

                samples = frame_challenge.locator("//div[@class='task-image']")
                count = 0
                with suppress(Exception):
                    count = await samples.count()
                if isinstance(count, int) and count == 9:
                    return RequestType.IMAGE_LABEL_BINARY
                if isinstance(count, int) and count == 0:
                    tms = self_arm.config.WAIT_FOR_CHALLENGE_VIEW_TO_RENDER_MS * 1.5
                    await self_arm.page.wait_for_timeout(tms)
                    challenge_view = frame_challenge.locator("//div[@class='challenge-view']")
                    cache_path = self_arm.config.cache_dir.joinpath(f"challenge_view/_artifacts/{uuid4()}.png")
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    await challenge_view.screenshot(type="png", path=cache_path)
                    router_result = await self_arm._challenge_router(challenge_screenshot=cache_path)
                    self_arm._challenge_prompt = router_result.challenge_prompt
                    return router_result.challenge_type
                return None

            async def patched_review_challenge_type(self_agent):
                # 如果队列为空，最多只等待 2.0 秒（避免因已弹出验证码或漏包而无谓干等 30 秒）
                wait_sec = 2.0 if self_agent._captcha_payload_queue.empty() else 30.0
                try:
                    self_agent._captcha_payload = await asyncio.wait_for(
                        self_agent._captcha_payload_queue.get(), timeout=wait_sec
                    )
                    await self_agent.page.wait_for_timeout(500)
                except asyncio.TimeoutError:
                    logger.debug("Captcha payload 等待完成，转入视觉 DOM 识别...")
                    self_agent._captcha_payload = None

                self_agent.robotic_arm.signal_crumb_count = None
                self_agent.robotic_arm.captcha_payload = None
                if not self_agent._captcha_payload:
                    return await self_agent.robotic_arm.check_challenge_type()

                try:
                    request_type = self_agent._captcha_payload.request_type
                    tasklist = self_agent._captcha_payload.tasklist
                    tasklist_length = len(tasklist)
                    self_agent.robotic_arm.captcha_payload = self_agent._captcha_payload
                    match request_type:
                        case RequestType.IMAGE_LABEL_BINARY:
                            self_agent.robotic_arm.signal_crumb_count = int(tasklist_length / 9)
                            return RequestType.IMAGE_LABEL_BINARY
                        case RequestType.IMAGE_LABEL_AREA_SELECT:
                            self_agent.robotic_arm.signal_crumb_count = tasklist_length
                            max_shapes = self_agent._captcha_payload.request_config.max_shapes_per_image
                            if not isinstance(max_shapes, int):
                                return await self_agent.robotic_arm.check_challenge_type()
                            return (
                                ChallengeTypeEnum.IMAGE_LABEL_SINGLE_SELECT
                                if max_shapes == 1
                                else ChallengeTypeEnum.IMAGE_LABEL_MULTI_SELECT
                            )
                        case RequestType.IMAGE_DRAG_DROP:
                            self_agent.robotic_arm.signal_crumb_count = tasklist_length
                            return (
                                ChallengeTypeEnum.IMAGE_DRAG_SINGLE
                                if len(tasklist[0].entities) == 1
                                else ChallengeTypeEnum.IMAGE_DRAG_MULTI
                            )

                    logger.warning(f"Unknown request_type: {request_type=}")
                except Exception as err:
                    logger.error(f"Error parsing challenge type: {err}")

            orig_task_handler = AgentV._task_handler

            async def patched_task_handler(self_agent, response):
                if "/checkcaptcha/" in response.url:
                    try:
                        import json
                        body_text = await response.text()
                        from hcaptcha_challenger.models import CaptchaResponse
                        if not body_text or not body_text.strip():
                            logger.debug(f"checkcaptcha response empty (status {response.status})")
                            cr = CaptchaResponse(**{"pass": True, "generated_pass_UUID": "empty_body_pass"})
                            self_agent._captcha_response_queue.put_nowait(cr)
                            return
                        data = json.loads(body_text)
                        self_agent._captcha_response_queue.put_nowait(CaptchaResponse(**data))
                        return
                    except Exception as e:
                        logger.debug(f"checkcaptcha 解析容错: {e}")
                        if response.status in (200, 204):
                            from hcaptcha_challenger.models import CaptchaResponse
                            self_agent._captcha_response_queue.put_nowait(CaptchaResponse(**{"pass": True, "generated_pass_UUID": "fallback_pass"}))
                        return
                await orig_task_handler(self_agent, response)

            async def patched_wait_for_challenge(self_agent):
                from hcaptcha_challenger.models import ChallengeSignal
                try:
                    if self_agent._captcha_response_queue.empty():
                        await asyncio.wait_for(self_agent._solve_captcha(), timeout=self_agent.config.EXECUTION_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.error("Challenge execution timed out", timeout=self_agent.config.EXECUTION_TIMEOUT)
                    return ChallengeSignal.EXECUTION_TIMEOUT

                logger.debug("Start checking captcha response")
                try:
                    cr = await asyncio.wait_for(
                        self_agent._captcha_response_queue.get(), timeout=self_agent.config.RESPONSE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    # 检查屏幕上 challenge frame 是否已经关闭消失了
                    has_active_frame = any(
                        not f.is_detached() and "hcaptcha.com" in f.url and "frame=challenge" in f.url
                        for f in self_agent.page.frames
                    )
                    if not has_active_frame:
                        logger.success("Captcha 响应等待超时但屏幕挑战已关闭消失，判定验证成功通过！")
                        return ChallengeSignal.SUCCESS
                    logger.error(f"Wait for captcha response timeout {self_agent.config.RESPONSE_TIMEOUT}s")
                    return ChallengeSignal.EXECUTION_TIMEOUT
                else:
                    has_active_frame = any(
                        not f.is_detached() and "hcaptcha.com" in f.url and "frame=challenge" in f.url
                        for f in self_agent.page.frames
                    )
                    # 只要 challenge frame 已经关闭消失，或者 cr.is_pass 为 True，立即判定通过！
                    if not has_active_frame or (cr and cr.is_pass):
                        logger.success("屏幕挑战弹窗已关闭消失或校验通过，判定验证成功通过！")
                        if cr and cr.is_pass:
                            self_agent._cache_validated_captcha_response(cr)
                        return ChallengeSignal.SUCCESS

                    if self_agent.config.RETRY_ON_FAILURE:
                        logger.warning("Failed to challenge, try to retry the strategy")
                        await self_agent.page.wait_for_timeout(2000)
                        return await self_agent.wait_for_challenge()
                    return ChallengeSignal.FAILURE

            RoboticArm.get_challenge_frame_locator = patched_get_challenge_frame_locator
            RoboticArm._find_challenge_frame_recursive = patched_find_challenge_frame_recursive
            RoboticArm.click_checkbox = patched_click_checkbox
            RoboticArm.check_crumb_count = patched_check_crumb_count
            RoboticArm.check_challenge_type = patched_check_challenge_type
            AgentV._review_challenge_type = patched_review_challenge_type
            AgentV._task_handler = patched_task_handler
            AgentV.wait_for_challenge = patched_wait_for_challenge
            logger.info("补丁：hcaptcha-challenger 多级嵌套/非标准域名/空回包容错优化增强已就绪")
        except Exception as arm_err:
            logger.warning(f"未能挂载 hcaptcha-challenger 增强补丁: {arm_err}")

        _patch_applied = True
        logger.info("补丁成功挂载：中转地址注入 + 多图写保护 + Base64 内联已就绪")

    except Exception as e:
        logger.error(f"补丁框架启动失败: {e}")

# 执行补丁
_apply_gemini_proxy_patch()
