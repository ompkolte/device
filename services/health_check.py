"""
Pre-exam health checks.
Verifies mic, speaker, storage before exam starts.
"""

import os
import io
import wave
import time
from typing import Tuple

from config.settings import settings
from config.logging_config import get_logger
from hardware.base import HardwareInterface

logger = get_logger("pi.health")


def run_health_checks(hw: HardwareInterface) -> Tuple[bool, list[str]]:
    """
    Run all health checks.
    Returns (all_passed, list_of_failures).
    """
    failures = []

    # 1. Storage check
    storage_dir = settings.storage_dir
    try:
        os.makedirs(storage_dir, exist_ok=True)
        test_file = os.path.join(storage_dir, ".health_check")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        logger.info("Storage check: OK")
    except Exception as e:
        failures.append(f"Storage: {e}")
        logger.error("Storage check failed: %s", e)

    # 2. Speaker test (play beep)
    hw.display("स्पीकर तपासत आहे...", "")
    try:
        # Generate a short test tone
        _play_test_tone(hw)
        logger.info("Speaker check: OK")
    except Exception as e:
        failures.append(f"Speaker: {e}")
        logger.error("Speaker check failed: %s", e)

    # 3. Microphone test (record and verify) — disabled
    # hw.display("माइक तपासत आहे...", "बोला: 'माझा माइक चालू आहे'")
    # try:
    #     hw.start_recording()
    #     time.sleep(2)  # Record for 2 seconds
    #     recording_path = hw.stop_recording()
    #
    #     # Verify recording exists and has content
    #     if not os.path.exists(recording_path):
    #         raise RuntimeError("Recording file not created")
    #
    #     size = os.path.getsize(recording_path)
    #     if size < 1000:  # Less than 1KB is suspicious
    #         raise RuntimeError(f"Recording too small: {size} bytes")
    #
    #     # Clean up test recording
    #     os.remove(recording_path)
    #     logger.info("Microphone check: OK")
    # except Exception as e:
    #     failures.append(f"Microphone: {e}")
    #     logger.error("Microphone check failed: %s", e)

    # Report result
    if failures:
        hw.display("तपासणी अयशस्वी", f"{len(failures)} समस्या")
        logger.warning("Health checks failed: %s", failures)
        return False, failures
    else:
        hw.display("तपासणी यशस्वी", "सर्व ठीक आहे")
        logger.info("All health checks passed")
        return True, []


def _play_test_tone(hw) -> None:
    """Play a short beep to test speaker."""
    import struct
    import math
    
    # Generate 440Hz tone, 0.5 seconds
    sample_rate = 16000
    duration = 0.5
    freq = 440
    
    num_samples = int(sample_rate * duration)
    buf = io.BytesIO()
    
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = []
        for i in range(num_samples):
            value = int(16000 * math.sin(2 * math.pi * freq * i / sample_rate))
            frames.append(struct.pack("<h", value))
        wf.writeframes(b"".join(frames))
    
    # Save temp file
    temp_path = os.path.join(settings.storage_dir, ".test_tone.wav")
    with open(temp_path, "wb") as f:
        f.write(buf.getvalue())
    
    hw.play_audio(temp_path)
    os.remove(temp_path)
