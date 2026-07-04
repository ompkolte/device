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

Simulator mode: skipped entirely (assumes network available).
"""

import os

from config.logging_config import get_logger
from config.settings import settings

logger = get_logger("pi.boot")

_WIFI_CONNECT_TIMEOUT = 15  # seconds


def run() -> None:
    """
    Execute the WiFi startup state machine.
    Blocks until the device has a working network connection.
    Skipped in simulator mode.
    """
    hardware_mode = os.environ.get("HARDWARE", "simulator").lower()
    if hardware_mode == "simulator":
        logger.info("Simulator mode — WiFi setup skipped.")
        return

    from startup.wifi_manager import (
        is_currently_connected,
        has_saved_wifi_profiles,
        try_connect,
    )

    # Fast path: already connected
    if is_currently_connected():
        logger.info("WiFi already connected.")
        return

    # Saved profiles exist → attempt connection
    if has_saved_wifi_profiles():
        logger.info("Saved WiFi profiles found — attempting connection (%ds timeout)…", _WIFI_CONNECT_TIMEOUT)
        if try_connect(timeout=_WIFI_CONNECT_TIMEOUT):
            return
        logger.warning("All saved WiFi profiles failed. Falling back to hotspot setup.")

    # No connection — run hotspot + captive portal loop until configured
    _hotspot_setup_loop()


def _hotspot_setup_loop() -> None:
    """
    Start hotspot → serve captive portal → connect to submitted WiFi.
    Loops until a successful connection is made.
    """
    from startup.wifi_manager import (
        start_hotspot,
        stop_hotspot,
        connect_to_wifi,
        is_currently_connected,
    )
    from startup.captive_portal import CaptivePortal

    ssid = settings.hotspot_ssid
    password = settings.hotspot_password
    portal_host = settings.captive_portal_host
    portal_port = settings.captive_portal_port

    while True:
        logger.info("Starting hotspot | ssid=%s", ssid)
        if not start_hotspot(ssid=ssid, password=password):
            logger.error("Could not start hotspot. Retrying in 10s…")
            import time
            time.sleep(10)
            continue

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

        logger.info("Connecting to WiFi | ssid=%s", credentials["ssid"])
        if connect_to_wifi(credentials["ssid"], credentials["password"]):
            logger.info("WiFi configured and connected.")
            return

        logger.error(
            "Failed to connect to '%s'. Restarting hotspot for retry.",
            credentials["ssid"],
        )
