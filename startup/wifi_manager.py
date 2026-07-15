"""
WiFi management via nmcli (Raspberry Pi OS / NetworkManager).
All functions are no-ops / return safe defaults on non-Linux systems.
"""

import subprocess
import time
from typing import Optional

from config.logging_config import get_logger

logger = get_logger("pi.wifi")

_WIFI_CONNECT_TIMEOUT = 20  # seconds


def is_currently_connected() -> bool:
    """Return True if wifi is connected AND has internet access."""
    try:
        # First check nmcli reports wifi connected
        result = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,STATE", "device"],
            capture_output=True, text=True, timeout=5,
        )
        if "wifi:connected" not in result.stdout:
            return False
        
        # Verify actual internet access (ping a reliable host)
        ping = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            capture_output=True, timeout=5,
        )
        return ping.returncode == 0
    except Exception as e:
        logger.debug("is_currently_connected check failed: %s", e)
        return False


def has_saved_wifi_profiles() -> bool:
    """Return True if NetworkManager has at least one saved WiFi profile."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE", "connection", "show"],
            capture_output=True, text=True, timeout=5,
        )
        return "802-11-wireless" in result.stdout
    except Exception as e:
        logger.debug("has_saved_wifi_profiles check failed: %s", e)
        return False


def try_connect(timeout: int = 15) -> bool:
    """
    Ask NetworkManager to bring up any saved WiFi connection.
    Polls for `timeout` seconds; returns True if connected.
    """
    try:
        subprocess.run(
            ["nmcli", "--wait", str(timeout), "device", "wifi", "connect"],
            capture_output=True, text=True, timeout=timeout + 5,
        )
    except Exception:
        pass  # nmcli may exit non-zero even on partial success; we poll below

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_currently_connected():
            logger.info("WiFi connected via saved profile.")
            return True
        time.sleep(2)

    logger.warning("WiFi connect timed out after %ds.", timeout)
    return False


def start_hotspot(ssid: str, password: str) -> bool:
    """
    Create an access point using nmcli.
    Requires NetworkManager >= 1.2 and wlan0 interface.
    """
    try:
        subprocess.run(
            [
                "nmcli", "device", "wifi", "hotspot",
                "ifname", "wlan0",
                "ssid", ssid,
                "password", password,
            ],
            capture_output=True, text=True, timeout=20, check=True,
        )
        logger.info("Hotspot started | ssid=%s", ssid)
        return True
    except subprocess.CalledProcessError as e:
        logger.error("Hotspot start failed: %s", e.stderr.strip())
        return False
    except Exception as e:
        logger.error("Hotspot start error: %s", e)
        return False


def stop_hotspot() -> None:
    """Tear down the nmcli-created hotspot connection."""
    try:
        subprocess.run(
            ["nmcli", "connection", "down", "Hotspot"],
            capture_output=True, text=True, timeout=10,
        )
        logger.info("Hotspot stopped.")
    except Exception as e:
        logger.warning("Failed to stop hotspot: %s", e)


def connect_to_wifi(ssid: str, password: str) -> bool:
    """Connect to a WiFi network using nmcli. Returns True on success."""
    try:
        result = subprocess.run(
            ["nmcli", "device", "wifi", "connect", ssid, "password", password],
            capture_output=True, text=True, timeout=_WIFI_CONNECT_TIMEOUT,
        )
        if result.returncode == 0:
            logger.info("WiFi connected | ssid=%s", ssid)
            return True
        logger.error("WiFi connect failed | ssid=%s | %s", ssid, result.stderr.strip())
        return False
    except Exception as e:
        logger.error("WiFi connect error: %s", e)
        return False
