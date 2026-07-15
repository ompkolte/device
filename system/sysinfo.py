"""
Collects system information automatically — no manual entry.
Works on Linux (Pi) and falls back gracefully on Windows/Mac for dev.
"""

import os
import platform
import socket
import uuid
from dataclasses import dataclass
from typing import Optional

from config.logging_config import get_logger

logger = get_logger("pi.sysinfo")


@dataclass
class SystemInfo:
    hostname: str
    mac_address: str
    ip_address: str
    os_version: str
    wifi_ssid: Optional[str]
    cpu_temp_celsius: Optional[float]
    ram_total_mb: Optional[int]
    ram_used_mb: Optional[int]
    disk_total_gb: Optional[float]
    disk_used_gb: Optional[float]


def collect() -> SystemInfo:
    return SystemInfo(
        hostname=_hostname(),
        mac_address=_mac(),
        ip_address=_ip(),
        os_version=_os_version(),
        wifi_ssid=_wifi_ssid(),
        cpu_temp_celsius=_cpu_temp(),
        ram_total_mb=_ram_total(),
        ram_used_mb=_ram_used(),
        disk_total_gb=_disk_total(),
        disk_used_gb=_disk_used(),
    )


def _hostname() -> str:
    return socket.gethostname()


def _mac() -> str:
    raw = uuid.getnode()
    return ":".join(f"{(raw >> (8 * i)) & 0xFF:02x}" for i in reversed(range(6)))


def _ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _os_version() -> str:
    return f"{platform.system()} {platform.release()} {platform.version()}"


def _wifi_ssid() -> Optional[str]:
    try:
        import subprocess
        result = subprocess.run(
            ["iwgetid", "-r"], capture_output=True, text=True, timeout=3
        )
        ssid = result.stdout.strip()
        return ssid if ssid else None
    except Exception:
        return None


def _cpu_temp() -> Optional[float]:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def _ram_total() -> Optional[int]:
    try:
        import shutil
        total, used, _ = shutil.disk_usage("/")
        # Use psutil if available for RAM
        import psutil
        vm = psutil.virtual_memory()
        return vm.total // (1024 * 1024)
    except Exception:
        return None


def _ram_used() -> Optional[int]:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return vm.used // (1024 * 1024)
    except Exception:
        return None


def _disk_total() -> Optional[float]:
    try:
        import shutil
        total, _, _ = shutil.disk_usage("/")
        return round(total / (1024 ** 3), 2)
    except Exception:
        return None


def _disk_used() -> Optional[float]:
    try:
        import shutil
        total, used, _ = shutil.disk_usage("/")
        return round(used / (1024 ** 3), 2)
    except Exception:
        return None
