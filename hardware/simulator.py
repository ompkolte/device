import io
import os
import sys
import time
import wave
import struct
import math
import threading

from .base import HardwareInterface

# Key → button mapping:  R=record  T=retry  N=next  P=repeat
_KEY_MAP = {"r": "record", "t": "retry", "n": "next", "p": "repeat"}

try:
    import sounddevice as sd
    import numpy as np
    _HAS_AUDIO = True
except ImportError:
    _HAS_AUDIO = False


def _make_silent_wav(duration_secs: float = 2.0, sample_rate: int = 16000) -> bytes:
    num_samples = int(duration_secs * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_samples)
    return buf.getvalue()


def _make_beep_wav(freq: float = 440.0, duration_secs: float = 0.5, sample_rate: int = 16000) -> bytes:
    num_samples = int(duration_secs * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = []
        for i in range(num_samples):
            value = int(32767 * math.sin(2 * math.pi * freq * i / sample_rate))
            frames.append(struct.pack("<h", value))
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


class SimulatorHardware(HardwareInterface):
    def __init__(self, recordings_dir: str = "./sim_recordings"):
        self._recordings_dir = recordings_dir
        os.makedirs(recordings_dir, exist_ok=True)
        self._recording = False
        self._frames: list = []
        self._stream = None

    def display(self, line1: str, line2: str = "") -> None:
        print(f"\n[DISPLAY] {line1}")
        if line2:
            print(f"          {line2}")

    def play_audio(self, wav_path: str, interruptible: bool = False) -> None:
        if not os.path.exists(wav_path):
            print(f"[AUDIO] (file not found: {wav_path})")
            return

        if _HAS_AUDIO:
            try:
                import soundfile as sf
                data, sr = sf.read(wav_path)
                sd.play(data, sr)
                sd.wait()
                return
            except Exception as e:
                print(f"[AUDIO] Playback error: {e}")

        # Fallback: just show duration
        try:
            with wave.open(wav_path, "rb") as wf:
                duration = wf.getnframes() / wf.getframerate()
            print(f"[AUDIO] Playing {os.path.basename(wav_path)} ({duration:.1f}s) ...")
            time.sleep(min(duration, 3.0))
        except Exception:
            print(f"[AUDIO] Playing {os.path.basename(wav_path)} ...")

    def start_recording(self) -> None:
        self._frames = []
        self._recording = True

        if _HAS_AUDIO:
            try:
                self._stream = sd.InputStream(
                    samplerate=16000,
                    channels=1,
                    dtype="int16",
                    callback=self._audio_callback,
                )
                self._stream.start()
                print("[REC] Recording from microphone... (press T to stop)")
                return
            except Exception as e:
                print(f"[REC] Microphone unavailable ({e}), will use silent audio.")

        print("[REC] Recording... (press T to stop)")

    def _audio_callback(self, indata, frames, time_info, status):
        if self._recording:
            self._frames.append(indata.copy())

    def stop_recording(self) -> str:
        self._recording = False
        timestamp = int(time.time())
        out_path = os.path.join(self._recordings_dir, f"rec_{timestamp}.ogg")

        if _HAS_AUDIO and self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
                self._stream = None
                if self._frames:
                    import numpy as np
                    import soundfile as sf
                    audio_data = np.concatenate(self._frames, axis=0)
                    sf.write(out_path, audio_data, 16000)
                    print(f"[REC] Saved to {out_path}")
                    return out_path
            except Exception as e:
                print(f"[REC] Error saving recording: {e}")

        # Fallback: write a short silent OGG
        if _HAS_AUDIO:
            try:
                import numpy as np
                import soundfile as sf
                silent = np.zeros(32000, dtype="int16")  # 2 sec silence
                sf.write(out_path, silent, 16000)
                print(f"[REC] Saved silent placeholder to {out_path}")
                return out_path
            except Exception:
                pass
        
        # Last resort: empty file
        out_path = out_path.replace(".ogg", ".wav")
        with open(out_path, "wb") as f:
            f.write(_make_silent_wav(2.0))
        print(f"[REC] Saved silent WAV placeholder to {out_path}")
        return out_path

    def wait_for_button(self) -> str:
        print("\n[KEYS] r=record  t=retry  n=next  p=repeat")
        while True:
            key = input(">>> ").strip().lower()
            if key in _KEY_MAP:
                return _KEY_MAP[key]
            print(f"      Unknown key '{key}'. Use r/t/n/p.")
