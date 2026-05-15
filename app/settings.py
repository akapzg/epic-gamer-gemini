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

        _patch_applied = True
        logger.info("补丁成功挂载：中转地址注入 + 多图写保护 + Base64 内联已就绪")

    except Exception as e:
        logger.error(f"补丁框架启动失败: {e}")

# 执行补丁
_apply_gemini_proxy_patch()
