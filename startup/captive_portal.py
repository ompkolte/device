"""
Minimal captive portal: serves a WiFi config form, blocks until submitted.
Uses stdlib http.server only — no extra dependencies.
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from config.logging_config import get_logger

logger = get_logger("pi.captive_portal")

_FORM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Exam Device — WiFi Setup</title>
  <style>
    body {{ font-family: sans-serif; max-width: 420px; margin: 48px auto; padding: 24px; color: #1f2937; }}
    h1   {{ font-size: 1.4rem; margin-bottom: 4px; }}
    p    {{ color: #6b7280; margin-bottom: 20px; }}
    label {{ display: block; font-size: .875rem; margin-bottom: 4px; }}
    input {{ width: 100%; padding: 10px; margin-bottom: 14px; border: 1px solid #d1d5db;
             border-radius: 6px; box-sizing: border-box; font-size: 1rem; }}
    button {{ width: 100%; padding: 12px; background: #2563eb; color: #fff;
              border: none; border-radius: 6px; font-size: 1rem; cursor: pointer; }}
    button:hover {{ background: #1d4ed8; }}
    .msg-ok  {{ background: #dcfce7; color: #166534; padding: 10px; border-radius: 6px; margin-bottom: 14px; }}
    .msg-err {{ background: #fee2e2; color: #991b1b; padding: 10px; border-radius: 6px; margin-bottom: 14px; }}
  </style>
</head>
<body>
  <h1>📡 Exam Device Setup</h1>
  <p>Connect this device to your WiFi network.</p>
  {message}
  <form method="POST" action="/connect">
    <label>WiFi Network (SSID)</label>
    <input type="text" name="ssid" placeholder="Network name" required autocomplete="off">
    <label>Password</label>
    <input type="password" name="password" placeholder="WiFi password" required>
    <button type="submit">Connect</button>
  </form>
</body>
</html>"""

_SUCCESS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Connected</title>
  <style>body{{ font-family:sans-serif; max-width:420px; margin:48px auto; padding:24px; text-align:center; }}</style>
</head>
<body>
  <h1>✅ Connecting…</h1>
  <p>Credentials received. The device is now connecting to WiFi and will register with the server.</p>
  <p style="color:#6b7280;font-size:.875rem;">You can close this page.</p>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    # Shared state written by POST, read by CaptivePortal.run_until_configured()
    _credentials: dict = {}
    _portal: "CaptivePortal" = None

    def do_GET(self) -> None:
        self._send_html(_FORM_HTML.format(message=""))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode(errors="replace")
        params = parse_qs(body)

        ssid = params.get("ssid", [""])[0].strip()
        password = params.get("password", [""])[0].strip()

        if not ssid:
            msg = '<div class="msg-err">WiFi name (SSID) is required.</div>'
            self._send_html(_FORM_HTML.format(message=msg))
            return

        _Handler._credentials = {"ssid": ssid, "password": password}
        self._send_html(_SUCCESS_HTML)
        # Shutdown from a thread so the response is fully sent first
        threading.Thread(target=_Handler._portal._stop, daemon=True).start()

    def _send_html(self, html: str) -> None:
        data = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # suppress default logging
        logger.debug("Portal request: " + fmt, *args)


class CaptivePortal:
    """
    Runs a blocking HTTP server until the user submits WiFi credentials.
    Returns the submitted {ssid, password} dict.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 80) -> None:
        self._host = host
        self._port = port
        self._server: HTTPServer | None = None

    def run_until_configured(self) -> dict:
        _Handler._credentials = {}
        _Handler._portal = self
        self._server = HTTPServer((self._host, self._port), _Handler)
        logger.info("Captive portal at http://%s:%d/ — waiting for WiFi credentials.", self._host, self._port)
        self._server.serve_forever()
        return _Handler._credentials

    def _stop(self) -> None:
        if self._server:
            self._server.shutdown()
