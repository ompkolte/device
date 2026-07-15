"""
Hardware abstraction layer.
MODE env var selects implementation: simulator (default) or raspberry
"""

import os
from .base import HardwareInterface


def get_hardware() -> HardwareInterface:
    """Returns the hardware implementation selected by the MODE env var."""
    mode = os.environ.get("MODE", "simulator").lower()

    if mode == "simulator":
        from .simulator import SimulatorHardware
        return SimulatorHardware()

    if mode == "raspberry":
        from .raspberry_pi import RaspberryPiHardware
        return RaspberryPiHardware()

    raise ValueError(f"Unknown MODE: {mode!r}. Use 'simulator' or 'raspberry'.")
