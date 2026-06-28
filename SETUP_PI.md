# Raspberry Pi Zero 2 W — Exam Device Setup

The Pi runs `exam_client.py` with `HARDWARE=raspberry_pi`. The **invigilator** types
the exam code / student code / DOB (USB keyboard or SSH); the **blind student** drives
the exam with 4 buttons guided by audio; the **OLED shows status for the invigilator**.

## 1. Hardware / wiring

| Part | Connection |
|------|-----------|
| USB sound card / headset | USB OTG port (mic + speaker) — Zero 2 W has no analog audio |
| SSD1306 OLED (I2C) | VCC→3V3, GND→GND, SDA→GPIO2 (pin 3), SCL→GPIO3 (pin 5) |
| Button: RECORD | GPIO17 ↔ GND |
| Button: RETRY/STOP | GPIO27 ↔ GND |
| Button: NEXT | GPIO22 ↔ GND |
| Button: REPEAT | GPIO10 ↔ GND |

Buttons use the Pi's internal pull-ups (no external resistors needed): one leg to the
GPIO pin, the other to GND. Override pins with `PIN_RECORD` / `PIN_RETRY` / `PIN_NEXT`
/ `PIN_REPEAT` env vars if you wire differently.

## 2. OS setup

```bash
sudo apt update
sudo apt install -y python3-pip libportaudio2 libopenjp2-7 libtiff6 i2c-tools libraqm0
# Enable I2C for the OLED:
sudo raspi-config   # → Interface Options → I2C → Enable, then reboot
```

Verify peripherals:
```bash
i2cdetect -y 1     # OLED should appear at 3c
aplay -l           # find the USB card (playback)
arecord -l         # find the USB card (capture)
```

## 3. Python deps

```bash
cd device
pip3 install -r requirements-pi.txt
```

## 4. Devanagari on the OLED

The status strings are Marathi. Drop a Devanagari TTF so they render:
```bash
# Option A: system font
sudo apt install -y fonts-noto-devanagari
# Option B: bundle it with the app
cp NotoSansDevanagari-Regular.ttf device/fonts/
```
`libraqm0` (installed above) gives proper matra/conjunct shaping. Override the font
path with `OLED_FONT=/path/to/font.ttf` if needed.

## 5. Audio cues for the student

By default the student hears **beeps**: a high beep when recording starts, a lower
beep when the answer is saved. To replace beeps with spoken Marathi prompts, drop
WAV files into `device/cues/` (no code change needed):

| File | Played when |
|------|-------------|
| `cues/record_start.wav` | recording begins |
| `cues/saved.wav` | an answer is saved |
| `cues/error.wav` | (reserved) error feedback |

You can generate these with the backend TTS service.

## 6. Run

```bash
cd device
HARDWARE=raspberry_pi \
  BACKEND_URL=http://<server-ip>:8000 \
  AUDIO_DEVICE=USB \
  python3 exam_client.py
```

- `AUDIO_DEVICE=USB` matches the USB card by name for both mic and speaker.
  For exact control use `AUDIO_INPUT_INDEX` / `AUDIO_OUTPUT_INDEX` (see `python3 -m sounddevice`).

## 7. Autostart on boot (optional)

`/etc/systemd/system/exam-device.service`:
```ini
[Unit]
Description=Exam Device Client
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/Exams-for-blind/device
Environment=HARDWARE=raspberry_pi
Environment=BACKEND_URL=http://<server-ip>:8000
Environment=AUDIO_DEVICE=USB
ExecStart=/usr/bin/python3 exam_client.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now exam-device
```
Note: the auth prompts read stdin, so for unattended kiosk use you'll want to adapt
`exam_client.py`'s auth step (e.g. read identity from a config/QR) rather than rely
on an interactive terminal under systemd.

## Button reference (student)

| Button | During question | While recording |
|--------|-----------------|-----------------|
| RECORD | start recording answer / submit at end | stop & save |
| RETRY  | — | stop, discard, re-record |
| NEXT   | skip question (no answer) | stop & move on |
| REPEAT | replay the question audio | (ignored) |
