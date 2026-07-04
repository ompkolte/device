"""
HTTP client for the Device Management backend.
Uses httpx with async support and automatic retries.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx

from config.settings import settings
from config.logging_config import get_logger
from system.identity import DeviceIdentity
from system.sysinfo import SystemInfo

logger = get_logger("pi.api_client")

_TIMEOUT = httpx.Timeout(10.0)
_MAX_RETRIES = 5
_RETRY_BACKOFF = 2.0  # seconds, doubles each attempt


async def register_device(identity: DeviceIdentity, sysinfo: SystemInfo) -> bool:
    """POST /api/devices/register — returns True on success."""
    payload = {
        "device_uuid": identity.device_uuid,
        "device_name": identity.device_name,
        "hostname": sysinfo.hostname,
        "mac_address": sysinfo.mac_address,
        "ip_address": sysinfo.ip_address,
        "software_version": settings.software_version,
        "os_version": sysinfo.os_version,
    }

    for attempt in range(1, _MAX_RETRIES + 1):
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
            return False
        except Exception as e:
            wait = _RETRY_BACKOFF * attempt
            logger.warning("Registration attempt %d failed (%s). Retrying in %.0fs.", attempt, e, wait)
            await asyncio.sleep(wait)

    logger.error("Registration failed after %d attempts.", _MAX_RETRIES)
    return False


async def send_heartbeat(identity: DeviceIdentity, sysinfo: SystemInfo) -> bool:
    """POST /api/devices/heartbeat — returns True on success."""
    payload = {
        "device_uuid": identity.device_uuid,
        "status": "online",
        "ip_address": sysinfo.ip_address,
        "hostname": sysinfo.hostname,
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
