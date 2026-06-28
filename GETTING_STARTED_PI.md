# Getting Started — Raspberry Pi Zero 2 W Exam Device (Complete Beginner Guide)

This guide takes you from a blank SD card to a working exam device, assuming **you
have never used a Raspberry Pi before**. Follow it top to bottom.

The exam device lets a blind student take an exam by speaking. The **invigilator**
types the exam code + student ID, the **student** uses 4 buttons guided by audio,
and the **OLED** shows live status for the invigilator.

---

## 0. What you need

**Parts you have:**
- Raspberry Pi Zero 2 W
- microSD card (8 GB or larger)
- SSD1306 OLED display (I2C, 4 pins: VCC, GND, SDA, SCL)
- 4 push buttons
- 4 resistors (~330 Ω; used as inline protection)
- USB headphones with microphone (this is your speaker **and** mic)
- USB micro → USB-A female OTG cable (to plug the headset into the Pi)

**Also required (get these if you don't have them):**
- 5 V micro-USB power supply (a phone charger works)
- A Windows/Mac/Linux computer to flash the card and to SSH in
- Jumper wires (female-to-female if the Pi has header pins) and ideally a breadboard
- WiFi network with **internet access** for the Pi
  - The Pi and your computer must be on the **same** network for SSH (Step 3).
  - The backend will run on **Hugging Face Spaces** (a public `https://` URL), so the
    Pi only needs general internet access to reach it — it does **not** have to be on
    the same network as the backend.

**Two things to check first:**

1. **Header pins.** Look at the long edge of the Pi. If there are metal pins sticking
   up (40 of them), good. If there are just bare holes, you must solder a 40-pin
   header (or fit a solderless "hammer header") before wiring. The "Zero 2 WH" comes
   with pins; the plain "Zero 2 W" does not.

2. **Is the OTG cable a real OTG cable?** It must have its "ID" pin grounded so the Pi
   acts as a USB host. A plain charge/extension cable won't enumerate the headset.
   If the headset doesn't show up later (Step 7), suspect this cable.

**About the single USB port:** The Pi Zero 2 W has only one usable USB data port.
During an exam you need the **headset** plugged in the whole time. So the invigilator
types the exam code/ID **over SSH from your computer** (recommended — no extra parts),
*not* on a keyboard plugged into the Pi. If you must use a physical keyboard on the
Pi, add a small USB hub so the keyboard and headset can both connect at once.

---

## 1. Flash Raspberry Pi OS onto the SD card

Do this on your computer.

1. Download and install **Raspberry Pi Imager** from https://www.raspberrypi.com/software/.
2. Insert the microSD card into your computer (use a card reader/adapter if needed).
   ⚠️ Flashing **erases everything** on the card.
3. Open Raspberry Pi Imager and choose:
   - **Choose Device:** `Raspberry Pi Zero 2 W`
   - **Choose OS:** `Raspberry Pi OS (other)` → **`Raspberry Pi OS Lite (32-bit)`**
     ("Lite" has no desktop — perfect for a headless device. **32-bit is recommended
     on the Zero 2 W**: it uses a bit less of the board's 512 MB RAM than 64-bit and
     installs the Python audio packages faster via prebuilt 32-bit wheels. The 64-bit
     Lite image also works if you prefer it.)
   - **Choose Storage:** your microSD card.
4. Click **Next**, then **Edit Settings** (this pre-configures the Pi so you never
   need a monitor):
   - **General tab:**
     - Set hostname: `exampi`
     - Set username and password: username `pi` and a password you'll remember.
     - Configure wireless LAN: your WiFi name (SSID), WiFi password, and your country.
     - Set locale / time zone.
   - **Services tab:**
     - ✅ Enable SSH → "Use password authentication".
   - Click **Save**.
5. Click **Yes** to apply settings, then **Yes** to erase and write. Wait until it
   finishes writing **and** verifying.
6. When done, eject the card and remove it.

---

## 2. First boot

1. Insert the microSD card into the Pi (slot on the underside).
2. Connect the 5 V power supply to the micro-USB port labeled **PWR IN** (closest to
   the corner). The *other* micro-USB is for USB devices — don't use it for power.
3. The green LED blinks as it boots. **First boot takes ~2 minutes** — be patient.
   The Pi joins your WiFi automatically using the settings from Step 1.

You do not need a monitor or keyboard on the Pi.

---

## 3. Connect to the Pi with SSH

"SSH" means opening a remote terminal on the Pi from your computer.

1. On your computer open a terminal:
   - **Windows:** open **PowerShell** (Start menu → type "PowerShell").
   - **Mac/Linux:** open **Terminal**.
2. Connect:
   ```
   ssh pi@exampi.local
   ```
3. First time, it asks to trust the device — type `yes` and press Enter.
4. Enter the password you set in Step 1.

You should now see a prompt like `pi@exampi:~ $`. **You are now typing commands on
the Pi.**

**If `exampi.local` doesn't work:**
- Find the Pi's IP address in your WiFi router's admin page (look for "exampi" in the
  connected-devices list), then connect with `ssh pi@192.168.x.x` using that IP.
- Make sure your computer and the Pi are on the same WiFi network.
- Wait a little longer after boot and try again.

---

## 4. Update the Pi and install system packages

Run these on the Pi (in the SSH window). Copy-paste one block at a time.

```bash
sudo apt update && sudo apt full-upgrade -y
```

```bash
sudo apt install -y python3-pip git i2c-tools \
  libportaudio2 libopenjp2-7 libtiff6 libraqm0 fonts-noto-devanagari
```

Enable I2C (the OLED talks to the Pi over I2C):
```bash
sudo raspi-config nonint do_i2c 0
```

Reboot so the changes take effect:
```bash
sudo reboot
```
This disconnects your SSH session. Wait ~1 minute, then reconnect:
```bash
ssh pi@exampi.local
```

---

## 5. Put the project code on the Pi

**If the project is on GitHub:**
```bash
cd ~
git clone <your-repo-url> Exams-for-blind
```

**If it's only on your computer:** open a terminal **on your computer** (a new window,
not the SSH one) and copy it over:
```
scp -r "F:\Exams for blind" pi@exampi.local:~/Exams-for-blind
```
(You can ignore/skip the `device/.venv` folder — it's Windows-only.)

Then back in the SSH window, install the Python dependencies:
```bash
cd ~/Exams-for-blind/device
pip3 install -r requirements-pi.txt --break-system-packages
```
(`--break-system-packages` is normal on recent Raspberry Pi OS; it lets pip install
for your user.)

---

## 6. Wire and test the OLED

> ⚠️ **Always power off before wiring.** Run `sudo poweroff`, wait for the LED to go
> dark, then unplug power. Never connect wires while the Pi is on.

The Pi's 40 pins are numbered: **pin 1** is the corner nearest the SD card. Odd
numbers are one row, even numbers the other.

| OLED pin | → Pi physical pin | what it is |
|----------|-------------------|------------|
| VCC / VDD | **pin 1** | 3.3 V power |
| GND | **pin 6** | ground |
| SDA | **pin 3** | GPIO2 (data) |
| SCL / SCK | **pin 5** | GPIO3 (clock) |

Power back on, SSH in, and check the Pi can see the OLED:
```bash
i2cdetect -y 1
```
You should see `3c` somewhere in the grid. If yes, run the display test:
```bash
cd ~/Exams-for-blind/device
python3 hardware_selftest.py oled
```
The screen should show **"OLED OK"** for 5 seconds.

**If `i2cdetect` shows nothing:** recheck the 4 OLED wires (especially SDA/SCL not
swapped), confirm I2C is enabled (Step 4), and that VCC goes to 3.3 V (pin 1), **not**
5 V.

---

## 7. Wire and test the buttons

Power off first (`sudo poweroff`, unplug).

The software turns on the Pi's **internal pull-up resistors**, so each button connects
a GPIO pin to ground; pressing it is read as a press. Use your 4 resistors as **inline
protection**: wire each button as **GPIO pin → resistor → button → GND**.

| Button | → GPIO (physical pin) | → GND (physical pin) |
|--------|-----------------------|----------------------|
| RECORD | GPIO17 (**pin 11**) | pin 9 |
| RETRY / STOP | GPIO27 (**pin 13**) | pin 14 |
| NEXT | GPIO22 (**pin 15**) | pin 20 |
| REPEAT | GPIO10 (**pin 19**) | pin 25 |

Power on, SSH in, and test:
```bash
cd ~/Exams-for-blind/device
python3 hardware_selftest.py buttons
```
Press each button — it prints which one, e.g. `pressed: record (GPIO17)`. You have 20
seconds.

**If a button does nothing:** recheck its two wires. **If the wrong name prints:** you
swapped two GPIO wires — fix the mapping above.

---

## 8. Connect and test the audio (USB headset)

1. Plug the USB headset into the **OTG cable**, and the OTG cable into the Pi's
   **middle micro-USB port** (the USB data port, *not* PWR IN).
2. SSH in and confirm the Pi sees the headset:
   ```bash
   aplay -l      # playback devices — your headset should be listed
   arecord -l    # capture (mic) devices — your headset should be listed
   ```
   If nothing USB shows up, the OTG cable is the likely problem (see Step 0).
3. Run the audio test (beeps, records 3 seconds, plays it back):
   ```bash
   cd ~/Exams-for-blind/device
   python3 hardware_selftest.py audio
   ```
   Say something during the recording. If you hear your own voice played back, mic +
   speaker both work.

It also prints a numbered device list. If auto-detect by name fails, note your card's
index numbers and use `AUDIO_INPUT_INDEX` / `AUDIO_OUTPUT_INDEX` in the next step.

---

## 9. Run a full test of everything

```bash
cd ~/Exams-for-blind/device
python3 hardware_selftest.py all
```
This runs OLED → buttons → audio in sequence. When all three pass, your hardware is
ready.

---

## 10. Run the real exam client

Make sure the **backend** is running and reachable from the Pi, then set `BACKEND_URL`
to point at it.

**Backend on Hugging Face Spaces (your setup):** use the Space's public URL — no port,
`https`, no trailing slash:
```bash
cd ~/Exams-for-blind/device
HARDWARE=raspberry_pi \
  BACKEND_URL=https://<your-username>-<your-space>.hf.space \
  AUDIO_DEVICE=USB \
  python3 exam_client.py
```
Find the URL on your Space page (the "Embed this Space" / direct link, e.g.
`https://janedoe-exam-backend.hf.space`). Confirm it works from the Pi first:
```bash
curl https://<your-username>-<your-space>.hf.space/health   # expect {"status":"ok"}
```

**Backend running locally instead (for testing on your LAN):** use the server's IP and
port `8000`, e.g. `BACKEND_URL=http://192.168.1.50:8000`.

- The **invigilator** types the exam code, student ID, and DOB **right here in the SSH
  window**.
- The device downloads the exam, then the **student** uses the 4 buttons:
  - **RECORD** — start recording the answer (and submit at the end)
  - **RETRY** — stop, discard, and re-record
  - **NEXT** — skip the question
  - **REPEAT** — play the question audio again
- The student hears a **high beep** when recording starts and a **lower beep** when the
  answer is saved. The **OLED** shows status for the invigilator.
- At the end, all answers upload to the server.

> Tip: if the headset isn't auto-detected, replace `AUDIO_DEVICE=USB` with explicit
> indices from Step 8, e.g. `AUDIO_INPUT_INDEX=1 AUDIO_OUTPUT_INDEX=1`.

---

## 11. (Optional) Spoken Marathi cues instead of beeps

Drop WAV files into `device/cues/` — no code change needed:
- `cues/record_start.wav` — played when recording begins
- `cues/saved.wav` — played when an answer is saved
- `cues/error.wav` — reserved for errors

You can generate these with the backend's text-to-speech.

---

## 12. (Optional) Start automatically on boot

So the device launches the exam client by itself when powered on.

Create the service file:
```bash
sudo nano /etc/systemd/system/exam-device.service
```
Paste this (set your server IP), then save with **Ctrl+O, Enter, Ctrl+X**:
```ini
[Unit]
Description=Exam Device Client
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/Exams-for-blind/device
Environment=HARDWARE=raspberry_pi
Environment=BACKEND_URL=https://<your-username>-<your-space>.hf.space
Environment=AUDIO_DEVICE=USB
ExecStart=/usr/bin/python3 exam_client.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
Enable it:
```bash
sudo systemctl enable --now exam-device
sudo systemctl status exam-device     # check it's running
```
> Note: auto-start can't accept typed input. For the typed-code workflow, keep running
> it manually over SSH, or adapt the auth step to read identity from a file/QR before
> using systemd.

---

## Troubleshooting quick reference

| Problem | Fix |
|---------|-----|
| `ssh: could not resolve hostname exampi.local` | Use the Pi's IP from your router instead. Same WiFi network? |
| OLED blank, `i2cdetect` empty | Check SDA/SCL not swapped, VCC on pin 1 (3.3 V), I2C enabled, then reboot. |
| Button prints wrong name | Two GPIO wires swapped — recheck the Step 7 table. |
| Headset not in `aplay -l` | OTG cable isn't a true OTG cable, or headset not fully seated. |
| Audio test silent | Wrong device index — use `AUDIO_INPUT_INDEX` / `AUDIO_OUTPUT_INDEX` from the printed list. |
| `pip3 install` refuses | Add `--break-system-packages` as shown in Step 5. |
| Can't reach backend (HF Spaces) | `curl https://<user>-<space>.hf.space/health` from the Pi should return `{"status":"ok"}`. If it hangs, the Space may be asleep — open it in a browser once to wake it, then retry. Check the Pi has internet (`ping -c1 hf.space`). |
