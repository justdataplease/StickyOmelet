from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import shutil
import sys
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gkeepapi
from gkeepapi import node as keep_node


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsSecret:
    """Protect secrets with Windows DPAPI for the current Windows user."""

    CRYPTPROTECT_UI_FORBIDDEN = 0x1

    @staticmethod
    def protect(value: str) -> str:
        if not value:
            return ""
        if sys.platform != "win32":
            raise RuntimeError("Secure credential storage requires Windows")
        raw = value.encode("utf-8")
        source_buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        source = _DataBlob(len(raw), source_buffer)
        destination = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        success = crypt32.CryptProtectData(
            ctypes.byref(source),
            "StickyOmelet",
            None,
            None,
            None,
            WindowsSecret.CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(destination),
        )
        if not success:
            raise ctypes.WinError()
        try:
            encrypted = ctypes.string_at(destination.pbData, destination.cbData)
            return base64.b64encode(encrypted).decode("ascii")
        finally:
            kernel32.LocalFree(destination.pbData)

    @staticmethod
    def unprotect(value: str) -> str:
        if not value:
            return ""
        if sys.platform != "win32":
            raise RuntimeError("Secure credential storage requires Windows")
        encrypted = base64.b64decode(value)
        source_buffer = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
        source = _DataBlob(len(encrypted), source_buffer)
        destination = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        success = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            WindowsSecret.CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(destination),
        )
        if not success:
            raise ctypes.WinError()
        try:
            decrypted = ctypes.string_at(destination.pbData, destination.cbData)
            return decrypted.decode("utf-8")
        finally:
            kernel32.LocalFree(destination.pbData)


class SettingsStore:
    def __init__(self) -> None:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        self.folder = base / "StickyOmelet"
        self.path = self.folder / "settings.json"
        self.folder.mkdir(parents=True, exist_ok=True)
        legacy_paths = (
            base / "StickyDot" / "settings.json",
            base / "StickyFeather" / "settings.json",
            base / "JustNotes" / "settings.json",
            base / "KeepNotesWidget" / "settings.json",
        )
        if not self.path.exists():
            for legacy_path in legacy_paths:
                if legacy_path.exists():
                    try:
                        shutil.copy2(legacy_path, self.path)
                        break
                    except OSError:
                        continue
        self.data: dict[str, Any] = {
            "version": 4,
            "email": "",
            "token_dpapi": "",
            "device_id": "",
            "topmost": True,
            "mode": "list",
            "selected_note_id": "",
            "geometry": "400x540+80+80",
            "bubble_position": "",
            "editor_font_size": 11,
            "list_filter": "all",
            "theme": "dark",
        }
        self.load()
        self._migrate()

    def _migrate(self) -> None:
        try:
            version = int(self.data.get("version", 0))
        except (TypeError, ValueError):
            version = 0
        if version >= 4:
            return
        geometry = str(self.data.get("geometry", ""))
        match = re.fullmatch(r"\d+x\d+([+-]\d+)([+-]\d+)", geometry)
        if match:
            self.data["geometry"] = f"400x540{match.group(1)}{match.group(2)}"
        else:
            self.data["geometry"] = "400x540+80+80"
        self.data["version"] = 4
        self.save()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key in self.data:
                    if key in loaded:
                        self.data[key] = loaded[key]
        except (OSError, json.JSONDecodeError):
            backup = self.path.with_suffix(".broken.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def save_credentials(self, email: str, master_token: str, device_id: str = "") -> None:
        self.data["email"] = email.strip()
        self.data["token_dpapi"] = WindowsSecret.protect(master_token.strip())
        self.data["device_id"] = device_id.strip()
        self.save()

    def credentials(self) -> tuple[str, str, str] | None:
        email = str(self.data.get("email", "")).strip()
        protected = str(self.data.get("token_dpapi", ""))
        if not email or not protected:
            return None
        try:
            return email, WindowsSecret.unprotect(protected), str(self.data.get("device_id", ""))
        except (ValueError, OSError, UnicodeDecodeError):
            return None

    def clear_credentials(self) -> None:
        self.data["email"] = ""
        self.data["token_dpapi"] = ""
        self.data["device_id"] = ""
        self.data["selected_note_id"] = ""
        self.save()


@dataclass(frozen=True)
class NoteRecord:
    id: str
    title: str
    body: str
    color: str
    pinned: bool
    is_list: bool
    updated: datetime
    labels: tuple[str, ...]


COLOR_ORDER = ("White", "Yellow", "Green", "Teal", "Blue", "DarkBlue", "Purple", "Pink", "Red", "Orange", "Brown", "Gray")


class KeepSyncClient:
    def __init__(self) -> None:
        self.keep: gkeepapi.Keep | None = None
        self.email = ""

    @property
    def connected(self) -> bool:
        return self.keep is not None

    def authenticate(self, email: str, master_token: str, device_id: str = "") -> list[NoteRecord]:
        keep = gkeepapi.Keep()
        keep.authenticate(email.strip(), master_token.strip(), sync=True, device_id=device_id or None)
        self.keep = keep
        self.email = email.strip()
        return self.records()

    def disconnect(self) -> None:
        self.keep = None
        self.email = ""

    def sync(self) -> list[NoteRecord]:
        keep = self._require_keep()
        keep.sync()
        return self.records()

    def records(self) -> list[NoteRecord]:
        keep = self._require_keep()
        records: list[NoteRecord] = []
        for item in keep.all():
            if getattr(item, "deleted", False) or getattr(item, "trashed", False) or getattr(item, "archived", False):
                continue
            is_list = isinstance(item, keep_node.List)
            body = self._list_text(item) if is_list else str(getattr(item, "text", "") or "")
            labels = tuple(sorted(label.name for label in item.labels.all()))
            updated = getattr(item.timestamps, "updated", None) or datetime.now(timezone.utc)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            color = getattr(getattr(item, "color", None), "name", "White")
            records.append(
                NoteRecord(
                    id=str(item.id),
                    title=str(item.title or ""),
                    body=body,
                    color=color,
                    pinned=bool(item.pinned),
                    is_list=is_list,
                    updated=updated,
                    labels=labels,
                )
            )
        records.sort(key=lambda note: (note.pinned, note.updated), reverse=True)
        return records

    def create_note(self) -> tuple[list[NoteRecord], str]:
        keep = self._require_keep()
        created = keep.createNote(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "")
        keep.sync()
        return self.records(), str(created.id)

    def create_list(self) -> tuple[list[NoteRecord], str]:
        keep = self._require_keep()
        created = keep.createList(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), [("New item", False)])
        keep.sync()
        return self.records(), str(created.id)

    def update_note(self, note_id: str, title: str, body: str) -> list[NoteRecord]:
        keep = self._require_keep()
        item = keep.get(note_id)
        if item is None:
            raise RuntimeError("This note no longer exists in Google Keep")
        item.title = title
        if isinstance(item, keep_node.List):
            self._update_list(item, body)
        else:
            item.text = body
        keep.sync()
        return self.records()

    def trash_note(self, note_id: str) -> list[NoteRecord]:
        keep = self._require_keep()
        item = keep.get(note_id)
        if item is None:
            raise RuntimeError("This note no longer exists in Google Keep")
        item.trash()
        keep.sync()
        return self.records()

    def toggle_pin(self, note_id: str) -> list[NoteRecord]:
        keep = self._require_keep()
        item = keep.get(note_id)
        if item is None:
            raise RuntimeError("This note no longer exists in Google Keep")
        item.pinned = not bool(item.pinned)
        keep.sync()
        return self.records()

    def cycle_color(self, note_id: str) -> list[NoteRecord]:
        keep = self._require_keep()
        item = keep.get(note_id)
        if item is None:
            raise RuntimeError("This note no longer exists in Google Keep")
        current = getattr(item.color, "name", "White")
        try:
            next_name = COLOR_ORDER[(COLOR_ORDER.index(current) + 1) % len(COLOR_ORDER)]
        except ValueError:
            next_name = "Yellow"
        item.color = getattr(keep_node.ColorValue, next_name)
        keep.sync()
        return self.records()

    def _require_keep(self) -> gkeepapi.Keep:
        if self.keep is None:
            raise RuntimeError("Google Keep is not connected")
        return self.keep

    @staticmethod
    def _list_text(item: keep_node.List) -> str:
        lines: list[str] = []
        for list_item in item.items:
            if getattr(list_item, "deleted", False):
                continue
            marker = "☑" if list_item.checked else "☐"
            lines.append(f"{marker} {list_item.text}")
        return "\n".join(lines)

    @staticmethod
    def _parse_list_body(body: str) -> list[tuple[str, bool]]:
        parsed: list[tuple[str, bool]] = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            checked = False
            if line.startswith("☑"):
                checked = True
                line = line[1:].strip()
            elif line.startswith("☐"):
                line = line[1:].strip()
            else:
                match = re.match(r"^\[([ xX])\]\s*(.*)$", line)
                if match:
                    checked = match.group(1).lower() == "x"
                    line = match.group(2).strip()
                elif line.startswith(("- ", "* ")):
                    line = line[2:].strip()
            if line:
                parsed.append((line, checked))
        return parsed

    def _update_list(self, item: keep_node.List, body: str) -> None:
        desired = self._parse_list_body(body)
        existing = [entry for entry in item.items if not getattr(entry, "deleted", False)]
        for index, (text, checked) in enumerate(desired):
            if index < len(existing):
                existing[index].text = text
                existing[index].checked = checked
            else:
                item.add(text, checked)
        for extra in existing[len(desired) :]:
            extra.delete()
