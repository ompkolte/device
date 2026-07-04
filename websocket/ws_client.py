"""
Persistent WebSocket connection to the Device Management backend.
Handles ping/pong, heartbeat-over-WS, and automatic reconnection.
"""

import asyncio
import json
from datetime import datetime, timezone

import websockets
from websockets.exceptions import ConnectionClosed

from config.settings import settings
from config.logging_config import get_logger
from system.identity import DeviceIdentity
from system.sysinfo import collect as collect_sysinfo

logger = get_logger("pi.websocket")


async def run_websocket(identity: DeviceIdentity, stop_event: asyncio.Event) -> None:
    """Maintain a persistent WS connection. Reconnects automatically."""
    url = f"{settings.ws_url}/ws/device/{identity.device_uuid}"

    while not stop_event.is_set():
        try:
            logger.info("Connecting WebSocket | url=%s", url)
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                logger.info("WebSocket connected.")
                await asyncio.gather(
                    _receive_loop(ws, identity, stop_event),
                    _heartbeat_loop(ws, identity, stop_event),
                )
        except ConnectionClosed as e:
            logger.warning("WebSocket closed (%s). Reconnecting in %ds.", e, settings.ws_reconnect_delay)
        except Exception as e:
            logger.error("WebSocket error: %s. Reconnecting in %ds.", e, settings.ws_reconnect_delay)

        if not stop_event.is_set():
            await asyncio.sleep(settings.ws_reconnect_delay)

    logger.info("WebSocket task stopped.")


async def _receive_loop(ws, identity: DeviceIdentity, stop_event: asyncio.Event) -> None:
    async for raw in ws:
        if stop_event.is_set():
            break
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        event = msg.get("event", "")
        if event == "pong":
            logger.debug("Pong received.")
        elif event == "heartbeat_ack":
            logger.debug("Heartbeat acknowledged via WS.")
        else:
            logger.debug("Unknown WS event: %s", event)


async def _heartbeat_loop(ws, identity: DeviceIdentity, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await asyncio.sleep(settings.heartbeat_interval)
        if stop_event.is_set():
            break
        sysinfo = collect_sysinfo()
        payload = json.dumps({
            "event": "heartbeat",
            "device_uuid": identity.device_uuid,
            "status": "online",
            "ip_address": sysinfo.ip_address,
            "hostname": sysinfo.hostname,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        try:
            await ws.send(payload)
            logger.debug("WS heartbeat sent | uuid=%s", identity.device_uuid)
        except Exception as e:
            logger.warning("WS heartbeat send failed: %s", e)
            break  # triggers reconnect in outer loop
