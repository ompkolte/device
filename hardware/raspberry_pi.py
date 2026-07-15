"""
Raspberry Pi Zero 2 W hardware implementation.

Role in the system
-------------------
- The INVIGILATOR enters exam code / student code / DOB over the Pi's stdin
  (USB keyboard or SSH). exam_client.py reads that via input().
- The OLED shows live device status FOR THE INVIGILATOR.
- The blind STUDENT operates the exam with the 4 physical buttons and is guided
  entirely by AUDIO: the spoken question (server TTS, played via play_audio) plus
  short beep cues for "recording started", "answer saved", and errors.

Hardware
--------
- Raspberry Pi Zero 2 W
- USB sound card / headset (the Zero 2 W has no analog audio) for mic + speaker
- SSD1306 128x64 OLED over I2C (SDA=GPIO2, SCL=GPIO3, addr 0x3C)
- 4 momentary push buttons to GPIO, each wired button-> GPIO pin and -> GND
  (internal pull-ups enabled, so pressing pulls the pin LOW)

Install on the Pi
-----------------
    sudo apt update
    sudo apt install -y python3-pip libportaudio2 libopenjp2-7 libtiff6 i2c-tools libraqm0
    pip3 install -r requirements-pi.txt
    # Enable I2C: sudo raspi-config -> Interface Options -> I2C -> Enable
    # Confirm OLED is on the bus:  i2cdetect -y 1   (expect 3c)
    # Confirm USB audio:           arecord -l  and  aplay -l

Run
---
    HARDWARE=raspberry_pi BACKEND_URL=http://<server>:8000 python3 exam_client.py

Configuration via environment variables (all optional)
------------------------------------------------------
    PIN_RECORD / PIN_RETRY / PIN_NEXT / PIN_REPEAT   BCM pin numbers (default 17/27/22/10)
    OLED_ADDR            I2C address of the OLED (default 0x3C)
    OLED_FONT            path to a .ttf with Devanagari glyphs for the OLED
    AUDIO_DEVICE         substring of the USB card name, e.g. "USB" (matches mic+speaker)
    AUDIO_INPUT_INDEX    explicit PortAudio input device index (overrides AUDIO_DEVICE)
    AUDIO_OUTPUT_INDEX   explicit PortAudio output device index (overrides AUDIO_DEVICE)
    CUES_DIR             folder of spoken cue WAVs (record_start.wav, saved.wav, error.wav)
"""

import io
import os
import time
import wave
import queue
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf
from gpiozero import Button
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from luma.core.render import canvas
from PIL import ImageFont, Image, ImageDraw

from .base import HardwareInterface

_DISPLAY_WIDTH = 128
_DISPLAY_HEIGHT = 64
_FONT_SIZE = 10
_LINE_HEIGHT = 15  # 4 lines: y=2, 17, 32, 47 fit in 64px
_LINE_Y = [2, 17, 32, 47]
_SCROLL_DELAY = 0.05  # seconds per pixel shift
_SCROLL_PAUSE = 0.8   # pause at end before returning

_SAMPLE_RATE = 16000   # mono 16-bit — matches what the backend STT expects
_CHANNELS = 1

# Default BCM pin numbers (override with env vars). Each button -> pin and -> GND.
_PIN_RECORD = int(os.environ.get("PIN_RECORD", 17))
_PIN_RETRY = int(os.environ.get("PIN_RETRY", 27))
_PIN_NEXT = int(os.environ.get("PIN_NEXT", 22))
_PIN_REPEAT = int(os.environ.get("PIN_REPEAT", 10))


def _resolve_audio_device(kind: str):
    """Return a PortAudio device index for 'input' or 'output'.

    Priority: explicit index env var -> name substring (AUDIO_DEVICE) -> None
    (None lets PortAudio pick its default, e.g. an ~/.asoundrc USB default).
    """
    idx_env = os.environ.get(
        "AUDIO_INPUT_INDEX" if kind == "input" else "AUDIO_OUTPUT_INDEX"
    )
    if idx_env is not None:
        return int(idx_env)

    name = os.environ.get("AUDIO_DEVICE")
    if not name:
        return None

    needed = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, dev in enumerate(sd.query_devices()):
        if name.lower() in dev["name"].lower() and dev[needed] > 0:
            return i
    print(f"[WARN] No {kind} device matching {name!r}; using PortAudio default.")
    return None


class RaspberryPiHardware(HardwareInterface):
    def __init__(self, recordings_dir: str = "/tmp/exam_recordings"):
        self._recordings_dir = recordings_dir
        os.makedirs(recordings_dir, exist_ok=True)
        self._cues_dir = os.environ.get("CUES_DIR", os.path.join(os.path.dirname(__file__), "..", "cues"))

        # ── Audio devices (USB card) ──────────────────────────────────────────
        self._in_dev = _resolve_audio_device("input")
        self._out_dev = _resolve_audio_device("output")

        # ── OLED ──────────────────────────────────────────────────────────────
        addr = int(os.environ.get("OLED_ADDR", "0x3C"), 16)
        serial = i2c(port=1, address=addr)
        self._oled = ssd1306(serial)
        self._font = self._load_font()

        # ── Buttons → a single queue so we can wait on "any of four" ──────────
        self._button_queue: "queue.Queue[str]" = queue.Queue()
        self._buttons = []
        for pin, label in (
            (_PIN_RECORD, "record"),
            (_PIN_RETRY, "retry"),
            (_PIN_NEXT, "next"),
            (_PIN_REPEAT, "repeat"),
        ):
            btn = Button(pin, pull_up=True, bounce_time=0.05)
            # default-arg binds label per-iteration
            btn.when_pressed = lambda lbl=label: self._button_queue.put(lbl)
            self._buttons.append(btn)

        # ── Recording state ───────────────────────────────────────────────────
        self._frames: list = []
        self._recording = False
        self._stream = None

    # ──────────────────────────────────────────────────────────────────────────
    # OLED
    # ──────────────────────────────────────────────────────────────────────────
    def _load_font(self):
        font_path = os.environ.get("OLED_FONT")
        candidates = [font_path] if font_path else []
        candidates += [
            os.path.join(os.path.dirname(__file__), "..", "fonts", "NotoSansDevanagari-Regular.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        ]
        for path in candidates:
            if path and os.path.exists(path):
                try:
                    return ImageFont.truetype(path, _FONT_SIZE)
                except Exception:
                    pass
        # Fallback: PIL's built-in bitmap font (Latin only — Devanagari shows as boxes).
        print("[WARN] No Devanagari TTF found; OLED Marathi text may not render. "
              "Set OLED_FONT or drop NotoSansDevanagari-Regular.ttf in device/fonts/.")
        return ImageFont.load_default()

    def _text_width(self, text: str) -> int:
        """Get pixel width of text string."""
        bbox = self._font.getbbox(text)
        return bbox[2] - bbox[0] if bbox else 0

    def _draw_static(self, lines: list[str]) -> None:
        """Draw up to 4 lines without scrolling."""
        with canvas(self._oled) as draw:
            for i, txt in enumerate(lines[:4]):
                if txt:
                    draw.text((2, _LINE_Y[i]), txt, font=self._font, fill="white")

    def _scroll_line(self, line_idx: int, text: str, other_lines: list[str], stop_flag: threading.Event) -> None:
        """Scroll a single line: left to end, pause, return to start."""
        text_w = self._text_width(text)
        max_offset = text_w - _DISPLAY_WIDTH + 4  # 4px margin
        if max_offset <= 0:
            return  # No scroll needed

        # Scroll left
        for offset in range(0, max_offset + 1, 2):
            if stop_flag.is_set():
                return
            img = Image.new("1", (128, 64), 0)
            draw = ImageDraw.Draw(img)
            for i, txt in enumerate(other_lines[:4]):
                if txt and i != line_idx:
                    draw.text((2, _LINE_Y[i]), txt, font=self._font, fill="white")
            draw.text((2 - offset, _LINE_Y[line_idx]), text, font=self._font, fill="white")
            self._oled.display(img)
            time.sleep(_SCROLL_DELAY)

        # Pause at end
        if not stop_flag.is_set():
            time.sleep(_SCROLL_PAUSE)

        # Return to start
        self._draw_static(other_lines[:line_idx] + [text] + other_lines[line_idx + 1:])

    def display(self, line1: str, line2: str = "", line3: str = "", line4: str = "") -> None:
        """Display up to 4 lines. Long lines scroll in background thread."""
        # Stop any existing scroll thread
        if hasattr(self, "_scroll_stop"):
            self._scroll_stop.set()
            if hasattr(self, "_scroll_thread") and self._scroll_thread.is_alive():
                self._scroll_thread.join(timeout=0.5)

        lines = [line1, line2, line3, line4]
        self._draw_static(lines)

        # Find lines that need scrolling
        scroll_needed = []
        for i, txt in enumerate(lines):
            if txt and self._text_width(txt) > _DISPLAY_WIDTH - 4:
                scroll_needed.append((i, txt))

        if not scroll_needed:
            return

        # Start background scroll thread
        self._scroll_stop = threading.Event()

        def scroll_all():
            for line_idx, text in scroll_needed:
                if self._scroll_stop.is_set():
                    return
                self._scroll_line(line_idx, text, lines, self._scroll_stop)

        self._scroll_thread = threading.Thread(target=scroll_all, daemon=True)
        self._scroll_thread.start()

    # ──────────────────────────────────────────────────────────────────────────
    # Audio playback + cues
    # ──────────────────────────────────────────────────────────────────────────
    def play_audio(self, wav_path: str) -> None:
        if not os.path.exists(wav_path):
            print(f"[AUDIO] file not found: {wav_path}")
            return
        try:
            data, sr = sf.read(wav_path, dtype="float32")
            sd.play(data, sr, device=self._out_dev)
            sd.wait()
        except Exception as e:
            print(f"[AUDIO] playback error: {e}")
        # Discard any button presses that landed during playback so the student's
        # real choice (made after hearing the question) is what counts.
        self._flush_buttons()

    def _tone(self, freq: float, dur: float = 0.15, vol: float = 0.3) -> None:
        t = np.linspace(0, dur, int(_SAMPLE_RATE * dur), endpoint=False)
        wave_f = (np.sin(2 * np.pi * freq * t) * vol).astype(np.float32)
        try:
            sd.play(wave_f, _SAMPLE_RATE, device=self._out_dev)
            sd.wait()
        except Exception as e:
            print(f"[AUDIO] tone error: {e}")

    def _cue(self, name: str, fallback_freq: float) -> None:
        """Play a spoken cue WAV if present (cues/<name>.wav), else a beep.

        Lets you upgrade beeps -> recorded Marathi prompts later with no code change.
        """
        path = os.path.join(self._cues_dir, f"{name}.wav")
        if os.path.exists(path):
            self.play_audio(path)
        else:
            self._tone(fallback_freq)

    # ──────────────────────────────────────────────────────────────────────────
    # Recording (USB mic, background stream → WAV)
    # ──────────────────────────────────────────────────────────────────────────
    def start_recording(self) -> None:
        self._frames = []
        self._recording = True
        self._cue("record_start", fallback_freq=880.0)  # rising "go" beep
        self._stream = sd.InputStream(
            samplerate=_SAMPLE_RATE,
            channels=_CHANNELS,
            dtype="int16",
            device=self._in_dev,
            callback=self._audio_callback,
        )
        self._stream.start()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[REC] stream status: {status}")
        if self._recording:
            self._frames.append(indata.copy())

    def stop_recording(self) -> str:
        self._recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        path = os.path.join(self._recordings_dir, f"rec_{int(time.time())}.ogg")
        audio = (
            np.concatenate(self._frames, axis=0)
            if self._frames
            else np.zeros((0, _CHANNELS), dtype="int16")
        )
        # Save as .ogg using soundfile
        sf.write(path, audio, _SAMPLE_RATE)
        return path

    # ──────────────────────────────────────────────────────────────────────────
    # Buttons
    # ──────────────────────────────────────────────────────────────────────────
    def _flush_buttons(self) -> None:
        while True:
            try:
                self._button_queue.get_nowait()
            except queue.Empty:
                return

    def wait_for_button(self) -> str:
        """Block until a button is pressed; returns 'record'|'retry'|'next'|'repeat'."""
        return self._button_queue.get()
