"""
Pi Device Management Client — entry point.

Startup sequence
----------------
1. Load / generate device identity (UUID)
2. Collect system information
3. Register with the Device Management backend (with retries)
4. Start HTTP heartbeat loop
5. Start WebSocket connection loop
6. Run until SIGINT / SIGTERM
"""

import asyncio
import os
import signal
import sys

from config.logging_config import setup_logging, get_logger
from config.settings import settings

# Show "SYSTEM ON" immediately on boot (before heavy imports)
def _show_system_on():
    mode = os.environ.get("MODE", "simulator").lower()
    if mode == "simulator":
        print("\n[DISPLAY] SYSTEM ON")
    else:
        try:
            # Direct OLED call — don't init full hardware (buttons would claim GPIO)
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            from luma.core.render import canvas
            addr = int(os.environ.get("OLED_ADDR", "0x3C"), 16)
            serial = i2c(port=1, address=addr)
            oled = ssd1306(serial)
            with canvas(oled) as draw:
                draw.text((2, 20), "SYSTEM ON", fill="white")
        except Exception as e:
            print(f"[WARN] Display init failed: {e}")

_show_system_on()

from startup.boot_flow import run as wifi_boot
from system.identity import load_or_create_identity, mark_registered
from system.sysinfo import collect as collect_sysinfo
from api.client import register_device
from services.heartbeat_service import run_http_heartbeat
from services.exam_service import ExamService
from websocket.ws_client import run_websocket, set_exam_service

setup_logging()
logger = get_logger("pi.main")


async def main() -> None:
    logger.info("=== Pi Device Management Client starting ===")
    logger.info("Backend: %s", settings.backend_url)
    logger.info("Mode: %s | Device: %s", settings.mode, settings.device_number)

    # ── WiFi startup ──────────────────────────────────────────────────────────
    wifi_boot()  # blocks until network is available; skipped in simulator mode

    # ── Identity ──────────────────────────────────────────────────────────────
    identity = load_or_create_identity()
    logger.info("Device UUID: %s", identity.device_uuid)

    # ── System info ───────────────────────────────────────────────────────────
    sysinfo = collect_sysinfo()
    logger.info("Hostname: %s | IP: %s | MAC: %s", sysinfo.hostname, sysinfo.ip_address, sysinfo.mac_address)

    # ── Registration ──────────────────────────────────────────────────────────
    registered = await register_device(identity, sysinfo)
    if not registered:
        logger.critical("Could not register with backend. Exiting.")
        sys.exit(1)
    mark_registered(identity)

    # ── Initialize exam service ───────────────────────────────────────────────
    exam_service = ExamService()
    set_exam_service(exam_service)
    logger.info("Exam service initialized")

    # ── Background tasks ──────────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _shutdown(*_):
        logger.info("Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for all signals
            signal.signal(sig, _shutdown)

    # Display ready status
    exam_service.hw.display(f"Device {settings.device_number}", "ONLINE - Waiting")

    await asyncio.gather(
        run_http_heartbeat(identity, stop_event),
        run_websocket(identity, stop_event),
    )

    logger.info("=== Pi Device Management Client stopped ===")


if __name__ == "__main__":
    asyncio.run(main())
