"""
Persistent WebSocket connection to the Device Management backend.
Handles ping/pong, heartbeat-over-WS, assignment reception, and automatic reconnection.
"""

import asyncio
import json

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from config.settings import settings
from config.logging_config import get_logger
from system.identity import DeviceIdentity
from system.sysinfo import collect as collect_sysinfo
from utils.timezone import now_ist

logger = get_logger("pi.websocket")

# Global exam service instance (set from main.py)
_exam_service = None
# Guard against concurrent exam runs
_exam_running = False
_exam_lock = asyncio.Lock()


def set_exam_service(svc):
    global _exam_service
    _exam_service = svc


async def _fetch_pending_assignment(device_uuid: str) -> dict | None:
    """Fetch any pending assignment on reconnect."""
    url = f"{settings.backend_url}/api/assignments/device/{device_uuid}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("package")
            return None
    except Exception as e:
        logger.warning("Failed to fetch pending assignment: %s", e)
        return None


async def run_websocket(identity: DeviceIdentity, stop_event: asyncio.Event) -> None:
    """Maintain a persistent WS connection. Reconnects automatically."""
    url = f"{settings.ws_url}/ws/device/{identity.device_uuid}"

    while not stop_event.is_set():
        try:
            logger.info("Connecting WebSocket | url=%s", url)
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                logger.info("WebSocket connected.")
                
                # On reconnect, check for pending assignment but DON'T auto-download
                # Wait for invigilator to click Continue or Reset
                pending = await _fetch_pending_assignment(identity.device_uuid)
                if pending and _exam_service and not _exam_service.assignment:
                    logger.info("Pending assignment found, waiting for invigilator action")
                    _exam_service.hw.display("पूर्वीचे सत्र", "इन्व्हिजिलेटरची वाट पहा")
                    # Store package but don't process yet
                    _exam_service._pending_package = pending
                
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
        elif event == "assignment":
            logger.info("Assignment received via WebSocket")
            await _handle_assignment(msg.get("package", {}))
        elif event == "exam_status":
            logger.info("Exam status update: %s -> %s", msg.get("exam_id"), msg.get("status"))
            await _handle_exam_status(msg.get("exam_id"), msg.get("status"))
        elif event == "continue":
            logger.info("Continue signal received, resuming session")
            await _handle_continue()
        elif event == "reset":
            logger.info("Reset signal received, clearing session")
            _handle_reset()
        else:
            logger.debug("Unknown WS event: %s", event)


async def _handle_assignment(package: dict) -> None:
    """Handle incoming assignment — download assets and wait for exam start."""
    if _exam_service is None:
        logger.error("Exam service not initialized")
        return

    # Run download in thread pool to avoid blocking async loop
    import concurrent.futures
    loop = asyncio.get_event_loop()
    
    def download_and_check():
        _exam_service.set_assignment(package)
        _exam_service.download_assets()
        
        # Run health checks
        if not _exam_service.run_health_check():
            logger.error("Health checks failed")
            return False
        return True
    
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            ready = await loop.run_in_executor(pool, download_and_check)
            if ready:
                logger.info("Device ready, waiting for exam start signal")
                _exam_service.hw.display("तयार आहे", "परीक्षा सुरू होण्याची वाट पहा")
                # ponytail: device now waits for exam_status event to run exam
    except Exception as e:
        logger.error("Download/check error: %s", e)


async def _handle_exam_status(exam_id: str, status: str) -> None:
    """Handle exam status change — start exam when active."""
    global _exam_running
    
    if _exam_service is None or _exam_service.assignment is None:
        return
    
    # Only care about our exam
    if _exam_service.assignment.get("exam_id") != exam_id:
        return
    
    if status == "active":
        # Guard against duplicate starts
        async with _exam_lock:
            if _exam_running:
                logger.warning("Exam already running, ignoring duplicate active signal")
                return
            _exam_running = True
        
        logger.info("Exam activated, starting exam flow")
        import concurrent.futures
        loop = asyncio.get_event_loop()
        
        def run_exam_blocking():
            return _exam_service.run_exam()
        
        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                answer_files = await loop.run_in_executor(pool, run_exam_blocking)
                logger.info("Exam completed: %d answers", len(answer_files))
        except Exception as e:
            logger.error("Exam error: %s", e)
        finally:
            async with _exam_lock:
                _exam_running = False


async def _handle_continue() -> None:
    """Handle continue signal — resume pending session."""
    if _exam_service is None:
        return
    
    pending = getattr(_exam_service, '_pending_package', None)
    if pending:
        logger.info("Continuing with pending assignment")
        _exam_service._pending_package = None
        await _handle_assignment(pending)
    else:
        logger.warning("No pending assignment to continue")


def _handle_reset() -> None:
    """Handle reset signal — clear session state."""
    if _exam_service is None:
        return
    
    _exam_service.assignment = None
    _exam_service._pending_package = None
    _exam_service.hw.display("रीसेट झाले", "नवीन सत्रासाठी तयार")
    logger.info("Device session reset")


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
            "timestamp": now_ist().isoformat(),
        })
        try:
            await ws.send(payload)
            logger.debug("WS heartbeat sent | uuid=%s", identity.device_uuid)
        except Exception as e:
            logger.warning("WS heartbeat send failed: %s", e)
            break  # triggers reconnect in outer loop
