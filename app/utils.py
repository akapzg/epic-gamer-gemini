# -*- coding: utf-8 -*-
"""
Epic Games Free Game Collection - Utilities

Common utility functions for logging, notifications, and time handling.

@Time    : 2026/05/01
@Author  : akapzg
@GitHub  : https://github.com/akapzg/epic-gamer-gemini
"""
from __future__ import annotations
import os
import sys
import httpx
from zoneinfo import ZoneInfo
from loguru import logger

def timezone_filter(record):
    record["time"] = record["time"].astimezone(ZoneInfo("Asia/Shanghai"))
    return record

async def send_bark_notification(title: str, body: str):
    """
    发送 Bark 通知
    """
    from settings import settings
    bark_url = settings.BARK_URL
    if not bark_url:
        logger.warning("⚠️ BARK_URL 未配置，跳过推送")
        return

    # 确保 URL 格式正确 (api.day.app/key)
    if not bark_url.startswith("http"):
        bark_url = f"https://{bark_url}"
    
    try:
        async with httpx.AsyncClient() as client:
            # 构造更丰富的推送内容
            payload = {
                "title": title,
                "body": f"账号: {settings.EPIC_EMAIL}\n{body}",
                "group": "epic-gamer",
                "icon": "https://raw.githubusercontent.com/akapzg/epic-gamer-gemini/main/Epicgames_A.png"
            }
            # 按照官方文档推荐，使用 JSON 格式发送
            resp = await client.post(bark_url, json=payload)
            if resp.status_code == 200:
                logger.info(f"🚀 Bark 推送成功 [{settings.EPIC_EMAIL}]: {body}")
            else:
                logger.error(f"❌ Bark 推送失败: {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"❌ Bark 推送过程中出现异常: {e}")

def init_log(**sink_channel):
    # 获取环境配置的日志级别，默认 DEBUG
    log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
    
    # 1. 配置控制台输出 (简洁、美观)
    # 格式: 2026-05-01 12:00:00 | INFO     | 🚀 任务开始...
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: ^8}</level> | "
        "<level>{message}</level>"
    )
    
    logger.remove()
    logger.add(sink=sys.stdout, level=log_level, format=console_format, filter=timezone_filter)
    
    # 2. 配置详细文件输出 (保留完整排错信息)
    # 格式: 2026-05-01 12:00:00.000 | DEBUG | app.deploy:main:123 - 详细信息
    file_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level: <8} | "
        "{name}:{function}:{line} - {message}"
    )

    if sink_channel.get("error"):
        logger.add(
            sink=sink_channel.get("error"), 
            level="ERROR", 
            format=file_format,
            rotation="5 MB", 
            filter=timezone_filter
        )
    if sink_channel.get("runtime"):
        logger.add(
            sink=sink_channel.get("runtime"), 
            level="DEBUG", 
            format=file_format,
            rotation="10 MB", 
            retention="7 days",
            filter=timezone_filter
        )
        
    return logger
