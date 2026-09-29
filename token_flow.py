from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import gpsoauth
import requests
import websocket


EMBEDDED_SETUP_URL = "https://accounts.google.com/EmbeddedSetup"


def find_chrome() -> Path | None:
    candidates: list[Path] = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return next((candidate for candidate in candidates if candidate.exists()), None)


def detect_google_email() -> str:
    """Best-effort prefill from Chrome preferences; never transmits the value."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return ""
    user_data = Path(local) / "Google" / "Chrome" / "User Data"
    profile_paths = [user_data / "Default", *sorted(user_data.glob("Profile *"))]
    for profile in profile_paths:
        preferences = profile / "Preferences"
        if not preferences.exists():
            continue
        try:
            data = json.loads(preferences.read_text(encoding="utf-8"))
            accounts = data.get("account_info", [])
            if accounts and isinstance(accounts[0], dict):
                email = str(accounts[0].get("email", "")).strip()
                if "@" in email:
                    return email
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return ""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_endpoint(port: int, endpoint: str = "json") -> Any:
    response = requests.get(f"http://127.0.0.1:{port}/{endpoint}", timeout=2)
    response.raise_for_status()
    return response.json()


def _cdp_command(websocket_url: str, method: str, command_id: int = 1) -> dict[str, Any]:
    connection = websocket.create_connection(websocket_url, timeout=3, origin="http://127.0.0.1")
    try:
        connection.send(json.dumps({"id": command_id, "method": method}))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            message = json.loads(connection.recv())
            if message.get("id") == command_id:
                return dict(message.get("result", {}))
        return {}
    finally:
        connection.close()


def _oauth_cookie(port: int) -> str:
    try:
        targets = _json_endpoint(port)
    except (requests.RequestException, ValueError):
        return ""
    for target in targets:
        if target.get("type") not in ("page", "webview") or not target.get("webSocketDebuggerUrl"):
            continue
        try:
            result = _cdp_command(str(target["webSocketDebuggerUrl"]), "Network.getAllCookies")
        except (OSError, ValueError, websocket.WebSocketException):
            continue
        for cookie in result.get("cookies", []):
            if cookie.get("name") == "oauth_token" and cookie.get("value"):
                return str(cookie["value"])
    return ""


def _close_browser(port: int, process: subprocess.Popen[Any]) -> None:
    try:
        version = _json_endpoint(port, "json/version")
        websocket_url = version.get("webSocketDebuggerUrl")
        if websocket_url:
            _cdp_command(str(websocket_url), "Browser.close", command_id=90)
    except (requests.RequestException, ValueError, OSError, websocket.WebSocketException):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()


def obtain_master_token(email: str, timeout_seconds: int = 300) -> tuple[str, str]:
    """Run Google's interactive setup in Chrome and exchange its cookie locally."""
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Google Chrome is required for automatic Keep connection")
    if "@" not in email:
        raise RuntimeError("Enter your Google account email first")

    port = _free_port()
    android_id = secrets.token_hex(8)
    auth_profile = Path(tempfile.mkdtemp(prefix="StickyOmeletAuth-"))
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            str(chrome),
            f"--user-data-dir={auth_profile}",
            "--incognito",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=560,760",
            EMBEDDED_SETUP_URL,
        ],
        creationflags=flags,
        close_fds=True,
    )
    try:
        deadline = time.monotonic() + timeout_seconds
        oauth_token = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Google sign-in was closed before it completed")
            oauth_token = _oauth_cookie(port)
            if oauth_token:
                break
            time.sleep(1)
        if not oauth_token:
            raise RuntimeError("Google sign-in timed out. Try Connect through Chrome again.")

        response = gpsoauth.exchange_token(email.strip(), oauth_token, android_id)
        master_token = str(response.get("Token", ""))
        if not master_token:
            detail = response.get("Error") or response.get("ErrorDetail") or "Google did not return a Keep token"
            raise RuntimeError(str(detail))
        return master_token, android_id
    finally:
        _close_browser(port, process)
        time.sleep(0.5)
        shutil.rmtree(auth_profile, ignore_errors=True)
