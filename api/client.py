"""
HTTP client for the Device Management backend.
Uses httpx with async support and automatic retries.
"""

import asyncio
from typing import Optional

import httpx

from config.settings import settings
from config.logging_config import get_logger
from system.identity import DeviceIdentity
from system.sysinfo import SystemInfo
from utils.timezone import now_ist

logger = get_logger("pi.api_client")

_TIMEOUT = httpx.Timeout(10.0)
_RETRY_DELAY = 5.0  # seconds between retries


async def register_device(identity: DeviceIdentity, sysinfo: SystemInfo) -> bool:
    """POST /api/devices/register — retries every 5s until success."""
    payload = {
        "device_uuid": identity.device_uuid,
        "device_number": settings.device_number,
        "device_name": identity.device_name,
        "hostname": sysinfo.hostname,
        "mac_address": sysinfo.mac_address,
        "ip_address": sysinfo.ip_address,
        "software_version": settings.software_version,
        "os_version": sysinfo.os_version,
    }

    attempt = 0
    while True:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(settings.register_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                if data.get("registered"):
                    logger.info("Registration successful | uuid=%s", identity.device_uuid)
                    return True
        except httpx.HTTPStatusError as e:
            logger.error("Registration HTTP error %s: %s", e.response.status_code, e.response.text)
        except Exception as e:
            logger.warning("Registration attempt %d failed. Retrying in 5s.", attempt)

        await asyncio.sleep(_RETRY_DELAY)


async def send_heartbeat(identity: DeviceIdentity, sysinfo: SystemInfo) -> bool:
    """POST /api/devices/heartbeat — returns True on success."""
    payload = {
        "device_uuid": identity.device_uuid,
        "status": "online",
        "ip_address": sysinfo.ip_address,
        "hostname": sysinfo.hostname,
        "timestamp": now_ist().isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(settings.heartbeat_url, json=payload)
            resp.raise_for_status()
            logger.debug("Heartbeat sent | uuid=%s", identity.device_uuid)
            return True
    except Exception as e:
        logger.warning("Heartbeat failed: %s", e)
        return False
