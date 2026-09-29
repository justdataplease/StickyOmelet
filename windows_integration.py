from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import winreg
except ImportError:  # pragma: no cover - StickyOmelet targets Windows
    winreg = None  # type: ignore[assignment]


class WindowsStartup:
    """Manage StickyOmelet's per-user Windows sign-in launch entry."""

    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    VALUE_NAME = "StickyOmelet"
    # Run values written by earlier brands of this app. They are carried over
    # to the current name so an existing "Start with Windows" choice survives.
    LEGACY_VALUE_NAMES = ("StickyDot",)

    @staticmethod
    def command() -> str:
        if getattr(sys, "frozen", False):
            arguments = [str(Path(sys.executable).resolve())]
        else:
            arguments = [str(Path(sys.executable).resolve()), str(Path(__file__).with_name("notes_widget.py").resolve())]
        return subprocess.list2cmdline(arguments)

    @classmethod
    def is_enabled(cls) -> bool:
        if sys.platform != "win32" or winreg is None:
            return False
        for name in (cls.VALUE_NAME, *cls.LEGACY_VALUE_NAMES):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.RUN_KEY, 0, winreg.KEY_READ) as key:
                    value, value_type = winreg.QueryValueEx(key, name)
            except (FileNotFoundError, OSError):
                continue
            if value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and bool(str(value).strip()):
                return True
        return False

    @classmethod
    def set_enabled(cls, enabled: bool) -> None:
        if sys.platform != "win32" or winreg is None:
            raise RuntimeError("Start with Windows is available only on Windows")
        if enabled:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cls.RUN_KEY) as key:
                winreg.SetValueEx(key, cls.VALUE_NAME, 0, winreg.REG_SZ, cls.command())
            cls._delete_values(cls.LEGACY_VALUE_NAMES)
            return
        cls._delete_values((cls.VALUE_NAME, *cls.LEGACY_VALUE_NAMES))

    @classmethod
    def migrate_legacy_entry(cls) -> None:
        """Rewrite a previous brand's Run value under the current name.

        The old value points at the old executable name, which stops existing
        once the user replaces it, so the launch would silently break."""
        if sys.platform != "win32" or winreg is None:
            return
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.RUN_KEY, 0, winreg.KEY_READ) as key:
                try:
                    winreg.QueryValueEx(key, cls.VALUE_NAME)
                    return  # Already on the current name; leave it alone.
                except FileNotFoundError:
                    pass
                legacy_present = False
                for name in cls.LEGACY_VALUE_NAMES:
                    try:
                        winreg.QueryValueEx(key, name)
                        legacy_present = True
                    except FileNotFoundError:
                        continue
        except (FileNotFoundError, OSError):
            return
        if legacy_present:
            try:
                cls.set_enabled(True)
            except OSError:
                pass

    @classmethod
    def _delete_values(cls, names: tuple[str, ...]) -> None:
        assert winreg is not None
        for name in names:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
