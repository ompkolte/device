import os
from hardware.base import HardwareInterface


def get_hardware() -> HardwareInterface:
    """Returns the hardware implementation selected by the HARDWARE env var."""
    mode = os.environ.get("HARDWARE", "simulator").lower()

    if mode == "simulator":
        from hardware.simulator import SimulatorHardware
        return SimulatorHardware()

    if mode == "raspberry_pi":
        from hardware.raspberry_pi import RaspberryPiHardware
        return RaspberryPiHardware()

    raise ValueError(f"Unknown HARDWARE mode: {mode!r}. Use 'simulator' or 'raspberry_pi'.")


BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
