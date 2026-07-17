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
import subprocess
import socket

import numpy as np
import sounddevice as sd
import soundfile as sf
from gpiozero import Button
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from luma.core.render import canvas
from PIL import ImageFont, Image, ImageDraw

from .base import HardwareInterface

_PING_HOST = "8.8.8.8"
_PING_INTERVAL = 3       # seconds between pings
_PING_FAIL_THRESHOLD = 2 # consecutive failures before Offline

_DISPLAY_WIDTH = 128
_DISPLAY_HEIGHT = 64
_FONT_SIZE = 10
_LINE_Y = [2, 18, 34, 50]  # 4 lines, 16px apart, font height ~10px
_SCROLL_DELAY = 0.05  # seconds per pixel shift
_SCROLL_PAUSE = 0.8   # pause at end before returning

_SAMPLE_RATE_CANDIDATES = (44100, 48000, 22050, 16000, 8000)
_CHANNELS = 1


def _detect_sample_rate(in_dev, out_dev) -> int:
    """Detect supported sample rate by actually opening test streams."""
    for sr in _SAMPLE_RATE_CANDIDATES:
        try:
            s = sd.OutputStream(device=out_dev, samplerate=sr, channels=_CHANNELS, dtype="float32")
            s.start()
            s.stop()
            s.close()
            # Also verify input
            s = sd.InputStream(device=in_dev, samplerate=sr, channels=_CHANNELS, dtype="int16")
            s.start()
            s.stop()
            s.close()
            return sr
        except Exception:
            continue
    return 44100

# Default BCM pin numbers (override with env vars). Each button -> pin and -> GND.
_PIN_RECORD = int(os.environ.get("PIN_RECORD", 17))
_PIN_RETRY = int(os.environ.get("PIN_RETRY", 27))
_PIN_NEXT = int(os.environ.get("PIN_NEXT", 22))
_PIN_REPEAT = int(os.environ.get("PIN_REPEAT", 23))


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
        self._sample_rate = _detect_sample_rate(self._in_dev, self._out_dev)

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
        self._rec_queue: queue.Queue = queue.Queue()
        self._rec_writer: threading.Thread = None
        self._rec_path: str = None

        # ── Persistent bar state ──────────────────────────────────────────────
        self._bar_online: bool = False
        self._bar_ip: str = self._get_ip()
        self._bar_student: str = "--"
        self._bar_exam: str = "--"
        self._ping_fail_count: int = 0

        # Start background ping thread
        self._ping_stop = threading.Event()
        threading.Thread(target=self._ping_loop, daemon=True).start()

    # ──────────────────────────────────────────────────────────────────────────
    # OLED
    # ──────────────────────────────────────────────────────────────────────────
    def _load_font(self):
        font_path = os.environ.get("OLED_FONT")
        candidates = [font_path] if font_path else []
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            os.path.join(os.path.dirname(__file__), "..", "fonts", "NotoSansDevanagari-Regular.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        ]
        for path in candidates:
            if path and os.path.exists(path):
                try:
                    return ImageFont.truetype(path, _FONT_SIZE)
                except Exception:
                    pass
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

    @staticmethod
    def _get_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "?"

    def _ping_loop(self) -> None:
        while not self._ping_stop.is_set():
            try:
                result = subprocess.run(
                    ["ping", "-c", "1", "-W", "2", _PING_HOST],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                if result.returncode == 0:
                    self._ping_fail_count = 0
                    if not self._bar_online:
                        self._bar_online = True
                        self._bar_ip = self._get_ip()
                        self._refresh_bar()
                else:
                    self._ping_fail_count += 1
                    if self._ping_fail_count >= _PING_FAIL_THRESHOLD and self._bar_online:
                        self._bar_online = False
                        self._refresh_bar()
            except Exception:
                self._ping_fail_count += 1
                if self._ping_fail_count >= _PING_FAIL_THRESHOLD and self._bar_online:
                    self._bar_online = False
                    self._refresh_bar()
            self._ping_stop.wait(_PING_INTERVAL)

    def _bar_lines(self) -> tuple[str, str]:
        line3 = f"Online {self._bar_ip}" if self._bar_online else "Offline"
        line4 = f"S:{self._bar_student} E:{self._bar_exam}"
        return line3, line4

    def _refresh_bar(self) -> None:
        """Redraw OLED keeping current line1/line2 (stored in _last_lines)."""
        l1, l2 = getattr(self, "_last_lines", ("", ""))
        l3, l4 = self._bar_lines()
        self._draw_static([l1, l2, l3, l4])

    def update_bar(self, student_id: str = None, exam_code: str = None) -> None:
        """Update persistent bar with assignment info and redraw."""
        if student_id is not None:
            self._bar_student = str(student_id)
        if exam_code is not None:
            self._bar_exam = str(exam_code)
        self._refresh_bar()

    def display(self, line1: str, line2: str = "") -> None:
        """Display lines 1-2; lines 3-4 are always the persistent bar."""
        # Stop any existing scroll thread
        if hasattr(self, "_scroll_stop"):
            self._scroll_stop.set()
            if hasattr(self, "_scroll_thread") and self._scroll_thread.is_alive():
                self._scroll_thread.join(timeout=0.5)

        self._last_lines = (line1, line2)
        l3, l4 = self._bar_lines()
        lines = [line1, line2, l3, l4]
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
    def play_audio(self, wav_path: str, interruptible: bool = False) -> None:
        if not os.path.exists(wav_path):
            print(f"[AUDIO] file not found: {wav_path}")
            return
        try:
            data, sr = sf.read(wav_path, dtype="float32")
            if sr != self._sample_rate:
                ratio = self._sample_rate / sr
                new_len = int(len(data) * ratio)
                data = np.interp(
                    np.linspace(0, len(data) - 1, new_len),
                    np.arange(len(data)),
                    data if data.ndim == 1 else data[:, 0],
                ).astype(np.float32)
            sd.play(data, self._sample_rate, device=self._out_dev)
            if interruptible:
                # Poll for button press — stop audio and put button back
                while sd.get_stream().active:
                    try:
                        btn = self._button_queue.get(timeout=0.05)
                        sd.stop()
                        self._button_queue.put(btn)  # put it back for FSM to handle
                        return
                    except queue.Empty:
                        continue
            else:
                sd.wait()
        except Exception as e:
            print(f"[AUDIO] playback error: {e}")
        self._flush_buttons()

    def _tone(self, freq: float, dur: float = 0.15, vol: float = 0.3) -> None:
        t = np.linspace(0, dur, int(self._sample_rate * dur), endpoint=False)
        wave_f = (np.sin(2 * np.pi * freq * t) * vol).astype(np.float32)
        try:
            sd.play(wave_f, self._sample_rate, device=self._out_dev)
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
        self._rec_path = os.path.join(self._recordings_dir, f"rec_{int(time.time())}.ogg")
        self._rec_queue = queue.Queue()
        self._recording = True

        # Writer thread drains queue and writes chunks incrementally
        def _writer():
            with sf.SoundFile(self._rec_path, mode="w", samplerate=self._sample_rate,
                              channels=_CHANNELS, format="OGG", subtype="VORBIS") as f:
                while True:
                    chunk = self._rec_queue.get()
                    if chunk is None:  # sentinel
                        break
                    f.write(chunk)

        self._rec_writer = threading.Thread(target=_writer, daemon=True)
        self._rec_writer.start()

        self._cue("record_start", fallback_freq=880.0)
        try:
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=_CHANNELS,
                dtype="int16",
                device=self._in_dev,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            print(f"[REC] Mic init failed: {e}")
            self._rec_queue.put(None)  # stop writer
            self._stream = None

    def _audio_callback(self, indata, frames, time_info, status):
        if self._recording:
            self._rec_queue.put(indata.copy())

    def stop_recording(self) -> str:
        self._recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        # Signal writer to finish and wait for file to close
        self._rec_queue.put(None)
        if self._rec_writer is not None:
            self._rec_writer.join()
        return self._rec_path

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
