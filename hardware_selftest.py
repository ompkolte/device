"""
Hardware self-test for the Pi exam device. Run each subsystem independently
so you can validate wiring step by step.

    python3 hardware_selftest.py oled       # draw text on the OLED
    python3 hardware_selftest.py buttons    # print each button press for 20s
    python3 hardware_selftest.py audio      # list devices, beep, record 3s, play back
    python3 hardware_selftest.py all        # everything in sequence

Pin defaults match raspberry_pi.py (override with the same env vars:
PIN_RECORD/PIN_RETRY/PIN_NEXT/PIN_REPEAT, OLED_ADDR, AUDIO_DEVICE, ...).
"""
import os
import sys
import time

PINS = {
    "record": int(os.environ.get("PIN_RECORD", 17)),
    "retry": int(os.environ.get("PIN_RETRY", 27)),
    "next": int(os.environ.get("PIN_NEXT", 22)),
    "repeat": int(os.environ.get("PIN_REPEAT", 10)),
}


def test_oled():
    print("\n=== OLED ===")
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    from luma.core.render import canvas
    from PIL import ImageFont

    addr = int(os.environ.get("OLED_ADDR", "0x3C"), 16)
    dev = ssd1306(i2c(port=1, address=addr))
    font = ImageFont.load_default()
    with canvas(dev) as draw:
        draw.text((2, 4), "OLED OK", font=font, fill="white")
        draw.text((2, 34), "exam device", font=font, fill="white")
    print(f"Drew text on OLED at {hex(addr)}. Look at the screen — it should show "
          "'OLED OK'. Cleared in 5s.")
    time.sleep(5)
    dev.clear()


def test_buttons():
    print("\n=== BUTTONS ===")
    from gpiozero import Button

    btns = []
    for label, pin in PINS.items():
        b = Button(pin, pull_up=True, bounce_time=0.05)
        b.when_pressed = lambda lbl=label, p=pin: print(f"  pressed: {lbl} (GPIO{p})")
        btns.append(b)
    print("Press each button. Listening for 20 seconds...")
    print(f"  mapping: {PINS}")
    time.sleep(20)
    print("Done listening.")


def test_audio():
    print("\n=== AUDIO ===")
    import numpy as np
    import sounddevice as sd

    print("Available audio devices:")
    print(sd.query_devices())

    name = os.environ.get("AUDIO_DEVICE")
    in_dev = int(os.environ["AUDIO_INPUT_INDEX"]) if os.environ.get("AUDIO_INPUT_INDEX") else None
    out_dev = int(os.environ["AUDIO_OUTPUT_INDEX"]) if os.environ.get("AUDIO_OUTPUT_INDEX") else None
    if name and in_dev is None or out_dev is None:
        for i, d in enumerate(sd.query_devices()):
            if name and name.lower() in d["name"].lower():
                if in_dev is None and d["max_input_channels"] > 0:
                    in_dev = i
                if out_dev is None and d["max_output_channels"] > 0:
                    out_dev = i

    sr = 16000
    print(f"\nUsing input={in_dev}, output={out_dev}. Playing a beep...")
    t = np.linspace(0, 0.4, int(sr * 0.4), endpoint=False)
    sd.play((np.sin(2 * np.pi * 880 * t) * 0.3).astype(np.float32), sr, device=out_dev)
    sd.wait()

    print("Recording 3 seconds — say something into the mic...")
    rec = sd.rec(int(3 * sr), samplerate=sr, channels=1, dtype="int16", device=in_dev)
    sd.wait()
    print("Playing it back...")
    sd.play(rec, sr, device=out_dev)
    sd.wait()
    print("If you heard your voice, mic + speaker both work.")


TESTS = {"oled": test_oled, "buttons": test_buttons, "audio": test_audio}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    targets = list(TESTS) if which == "all" else [which]
    for name in targets:
        if name not in TESTS:
            print(f"Unknown test {name!r}. Use: {', '.join(TESTS)} or 'all'.")
            continue
        try:
            TESTS[name]()
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
