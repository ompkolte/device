"""
Boot-time WiFi state machine.

Power ON
    │
    ▼
Is WiFi configured? (saved profiles in NetworkManager)
    │
 ┌──┴──────────┐
 │             │
No            Yes
 │             │
 ▼             ▼
Create      Try connecting (15s timeout)
Hotspot         │
 │         ┌───┴──────┐
 │         │          │
 │     Connected   Failed
 │         │          │
 │         ▼          ▼
 │      Proceed    Create Hotspot
 │
 ▼
User connects to hotspot → opens http://192.168.4.1
 │
 ▼
Enter WiFi credentials → connect → proceed

FORCE_HOTSPOT mode: if connected SSID == FORCE_HOTSPOT_SSID, ignore and go to hotspot.
Simulator mode: skipped entirely (assumes network available).
"""

import os
import time
from PIL import ImageFont

from config.logging_config import get_logger
from config.settings import settings

logger = get_logger("pi.boot")

_WIFI_CONNECT_TIMEOUT = 15
_BOOT_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_BOOT_FONT_SIZE = 10
_LINE_Y = [2, 18, 34, 50]

_boot_oled = None


def _load_boot_font():
    try:
        return ImageFont.truetype(_BOOT_FONT_PATH, _BOOT_FONT_SIZE)
    except Exception:
        return ImageFont.load_default()


def _get_display():
    """Get display function using direct OLED (no full hardware init)."""
    mode = os.environ.get("MODE", "simulator").lower()
    if mode == "simulator":
        def _sim_display(line1, line2=""):
            print(f"[DISPLAY] {line1}")
            if line2:
                print(f"          {line2}")
        return _sim_display
    else:
        try:
            global _boot_oled
            if _boot_oled is None:
                from luma.core.interface.serial import i2c
                from luma.oled.device import ssd1306
                addr = int(os.environ.get("OLED_ADDR", "0x3C"), 16)
                serial = i2c(port=1, address=addr)
                _boot_oled = ssd1306(serial)

            font = _load_boot_font()

            def _oled_display(line1, line2=""):
                from luma.core.render import canvas
                with canvas(_boot_oled) as draw:
                    for i, ln in enumerate((line1, line2)):
                        if ln:
                            draw.text((2, _LINE_Y[i]), ln, font=font, fill="white")
            return _oled_display
        except Exception as e:
            logger.warning("Boot display init failed: %s", e)
            return lambda *args, **kwargs: None


def _get_connected_ssid() -> str:
    """Return currently connected WiFi SSID, or empty string."""
    try:
        import subprocess
        result = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=3)
        return result.stdout.strip()
    except Exception:
        return ""


def run() -> None:
    """
    Execute the WiFi startup state machine.
    Blocks until the device has a working network connection.
    Skipped in simulator mode.
    """
    hardware_mode = os.environ.get("MODE", "simulator").lower()
    if hardware_mode == "simulator":
        logger.info("Simulator mode — WiFi setup skipped.")
        return

    display = _get_display()

    from startup.wifi_manager import (
        is_currently_connected,
        has_saved_wifi_profiles,
        try_connect,
    )

    display("Checking WiFi", "Please wait...")

    # FORCE_HOTSPOT: if connected to the test SSID, ignore it and go to hotspot
    if settings.force_hotspot:
        connected_ssid = _get_connected_ssid()
        if connected_ssid and connected_ssid == settings.force_hotspot_ssid:
            logger.info("FORCE_HOTSPOT: connected to %r — ignoring, going to hotspot.", connected_ssid)
            display("Force Hotspot", f"Ignoring {connected_ssid}")
            _hotspot_setup_loop(display)
            return
        # Connected to a different SSID — proceed normally
        logger.info("FORCE_HOTSPOT set but connected to %r (not %r) — proceeding normally.",
                    connected_ssid, settings.force_hotspot_ssid)

    # Fast path: already connected
    if is_currently_connected():
        logger.info("WiFi already connected.")
        display("WiFi Connected", "Starting...")
        return

    # Saved profiles exist → attempt connection
    if has_saved_wifi_profiles():
        display("Connecting...", "Please wait...")
        logger.info("Saved WiFi profiles found — attempting connection (%ds timeout)…", _WIFI_CONNECT_TIMEOUT)
        if try_connect(timeout=_WIFI_CONNECT_TIMEOUT):
            display("WiFi Connected", "Starting...")
            return
        logger.warning("All saved WiFi profiles failed. Falling back to hotspot setup.")

    # No connection — run hotspot + captive portal loop until configured
    _hotspot_setup_loop(display)


def _hotspot_setup_loop(display) -> None:
    """
    Start hotspot → serve captive portal → connect to submitted WiFi.
    Loops until a successful connection is made.
    """
    from startup.wifi_manager import (
        start_hotspot,
        stop_hotspot,
        connect_to_wifi,
    )
    from startup.captive_portal import CaptivePortal

    ssid = settings.hotspot_ssid
    password = settings.hotspot_password
    portal_host = settings.captive_portal_host
    portal_port = settings.captive_portal_port

    while True:
        logger.info("Starting hotspot | ssid=%s", ssid)
        display("Hotspot Active", ssid)
        if not start_hotspot(ssid=ssid, password=password):
            logger.error("Could not start hotspot. Retrying in 10s…")
            display("Hotspot Failed", "Retry in 10s")
            time.sleep(10)
            continue

        display("Open Browser", f"{portal_host}:{portal_port}")
        logger.info(
            "Connect to '%s' and open http://%s:%d/ to configure WiFi.",
            ssid, portal_host, portal_port,
        )

        portal = CaptivePortal(host=portal_host, port=portal_port)
        credentials = portal.run_until_configured()

        logger.info("Credentials received — stopping hotspot.")
        stop_hotspot()

        if not credentials:
            logger.warning("No credentials received. Restarting hotspot.")
            continue

        ssid_short = credentials["ssid"][:16] + ".." if len(credentials["ssid"]) > 18 else credentials["ssid"]
        display("Connecting...", ssid_short)
        logger.info("Connecting to WiFi | ssid=%s", credentials["ssid"])
        if connect_to_wifi(credentials["ssid"], credentials["password"]):
            display("WiFi Connected", "Starting...")
            logger.info("WiFi configured and connected.")
            return

        display("Connect Failed", "Retry...")
        logger.error(
            "Failed to connect to '%s'. Restarting hotspot for retry.",
            credentials["ssid"],
        )
