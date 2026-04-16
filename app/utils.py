# -*- coding: utf-8 -*-
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
            payload = {
                "title": title,
                "body": body,
                "group": "epic-gamer"
            }
            resp = await client.post(bark_url, data=payload)
            if resp.status_code == 200:
                logger.info(f"🚀 Bark 推送成功: {body}")
            else:
                logger.error(f"❌ Bark 推送失败: {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"❌ Bark 推送过程中出现异常: {e}")

def init_log(**sink_channel):
    # 简单的日志初始化，不再包含任何补丁逻辑
    log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
    logger.remove()
    logger.add(sink=sys.stdout, level=log_level, filter=timezone_filter)
    
    # 挂载其他日志输出
    if sink_channel.get("error"):
        logger.add(sink=sink_channel.get("error"), level="ERROR", rotation="5 MB", filter=timezone_filter)
    if sink_channel.get("runtime"):
        logger.add(sink=sink_channel.get("runtime"), level="TRACE", rotation="5 MB", filter=timezone_filter)
        
    return logger
