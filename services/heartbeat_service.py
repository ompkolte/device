"""
HTTP heartbeat service — runs alongside the WebSocket heartbeat as a fallback.
If the WS is healthy the WS heartbeat is preferred; this ensures the REST
endpoint also stays current (useful if the WS drops briefly).
"""

import asyncio

from config.settings import settings
from config.logging_config import get_logger
from system.identity import DeviceIdentity
from system.sysinfo import collect as collect_sysinfo
from api.client import send_heartbeat

logger = get_logger("pi.heartbeat")


async def run_http_heartbeat(identity: DeviceIdentity, stop_event: asyncio.Event) -> None:
    logger.info("HTTP heartbeat service started | interval=%ds", settings.heartbeat_interval)
    while not stop_event.is_set():
        await asyncio.sleep(settings.heartbeat_interval)
        if stop_event.is_set():
            break
        sysinfo = collect_sysinfo()
        ok = await send_heartbeat(identity, sysinfo)
        if not ok:
            logger.warning("HTTP heartbeat failed — will retry next cycle.")
    logger.info("HTTP heartbeat service stopped.")
