"""
Device identity — generates UUID on first boot, persists to device.json.
"""

import json
import os
import uuid
from dataclasses import dataclass, asdict
from typing import Optional

from config.settings import settings
from config.logging_config import get_logger

logger = get_logger("pi.identity")


@dataclass
class DeviceIdentity:
    device_uuid: str
    device_name: str = "Exam Device"
    registered: bool = False


def load_or_create_identity() -> DeviceIdentity:
    path = os.path.abspath(settings.device_config_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            identity = DeviceIdentity(**data)
            logger.info("Loaded device identity | uuid=%s", identity.device_uuid)
            return identity
        except Exception as e:
            logger.warning("Failed to read device.json (%s), regenerating.", e)

    identity = DeviceIdentity(device_uuid=str(uuid.uuid4()))
    _save(identity, path)
    logger.info("Generated new device identity | uuid=%s", identity.device_uuid)
    return identity


def mark_registered(identity: DeviceIdentity) -> None:
    identity.registered = True
    _save(identity, os.path.abspath(settings.device_config_path))


def _save(identity: DeviceIdentity, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(identity), f, indent=2)
