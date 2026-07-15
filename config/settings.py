"""
Pi-client settings loaded from environment variables / .env file.
No hardcoded URLs or IPs.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class PiSettings:
    backend_url: str = os.environ.get("BACKEND_URL", "http://localhost:8001")
    main_backend_url: str = os.environ.get("MAIN_BACKEND_URL", "http://localhost:8000")
    software_version: str = os.environ.get("SOFTWARE_VERSION", "1.0.0")

    # Hardware mode: simulator or raspberry
    mode: str = os.environ.get("MODE", "simulator")

    # Permanent device number (D001, D002, etc.) — set once per device
    device_number: str = os.environ.get("DEVICE_NUMBER", "D001")

    # Heartbeat interval in seconds
    heartbeat_interval: int = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))

    # WebSocket reconnect delay in seconds
    ws_reconnect_delay: int = int(os.environ.get("WS_RECONNECT_DELAY", "5"))

    # Hotspot / captive portal (used when WiFi is not yet configured)
    hotspot_ssid: str = os.environ.get("HOTSPOT_SSID", "ExamDevice-Setup")
    hotspot_password: str = os.environ.get("HOTSPOT_PASSWORD", "examsetup123")
    captive_portal_host: str = os.environ.get("CAPTIVE_PORTAL_HOST", "192.168.4.1")
    captive_portal_port: int = int(os.environ.get("CAPTIVE_PORTAL_PORT", "80"))

    # Path to persist device identity
    device_config_path: str = os.environ.get(
        "DEVICE_CONFIG_PATH",
        os.path.join(os.path.dirname(__file__), "..", "config", "device.json"),
    )

    # Local storage for exam data
    storage_dir: str = os.environ.get(
        "STORAGE_DIR",
        os.path.join(os.path.dirname(__file__), "..", "storage"),
    )

    log_dir: str = os.environ.get("LOG_DIR", os.path.join(os.path.dirname(__file__), "..", "logs"))
    log_level: str = os.environ.get("LOG_LEVEL", "INFO")

    @property
    def register_url(self) -> str:
        return f"{self.backend_url}/api/devices/register"

    @property
    def heartbeat_url(self) -> str:
        return f"{self.backend_url}/api/devices/heartbeat"

    @property
    def ws_url(self) -> str:
        # http -> ws, https -> wss
        base = self.backend_url.replace("http://", "ws://").replace("https://", "wss://")
        return base


settings = PiSettings()
