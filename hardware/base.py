from abc import ABC, abstractmethod


class HardwareInterface(ABC):
    @abstractmethod
    def play_audio(self, wav_path: str, interruptible: bool = False) -> None: ...

    @abstractmethod
    def start_recording(self) -> None: ...

    @abstractmethod
    def stop_recording(self) -> str: ...
    """Returns local path to the saved WAV file."""

    @abstractmethod
    def wait_for_button(self) -> str: ...
    """Returns one of: 'record' | 'retry' | 'next' | 'repeat'"""

    @abstractmethod
    def display(self, line1: str, line2: str = "", line3: str = "", line4: str = "") -> None: ...
    """OLED display on Pi (4 lines); console print in simulator."""
