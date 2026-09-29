from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import windows_integration
from windows_integration import WindowsStartup


def fake_winreg() -> MagicMock:
    registry = MagicMock()
    registry.HKEY_CURRENT_USER = object()
    registry.KEY_READ = 1
    registry.KEY_SET_VALUE = 2
    registry.REG_SZ = 3
    registry.REG_EXPAND_SZ = 4
    return registry


class WindowsStartupTests(unittest.TestCase):
    def test_frozen_command_quotes_executable_path(self) -> None:
        executable = r"C:\Program Files\StickyOmelet\StickyOmelet.exe"
        with patch.object(windows_integration.sys, "frozen", True, create=True), patch.object(
            windows_integration.sys, "executable", executable
        ):
            self.assertEqual(subprocess.list2cmdline([str(Path(executable).resolve())]), WindowsStartup.command())

    def test_source_command_includes_python_and_entry_script(self) -> None:
        with patch.object(windows_integration.sys, "frozen", False, create=True):
            command = WindowsStartup.command()
        self.assertIn(Path(sys.executable).name, command)
        self.assertIn("notes_widget.py", command)

    def test_is_enabled_reads_current_user_run_value(self) -> None:
        registry = fake_winreg()
        key = object()
        registry.OpenKey.return_value.__enter__.return_value = key
        registry.QueryValueEx.return_value = (r'"C:\Apps\StickyOmelet.exe"', registry.REG_SZ)
        with patch.object(windows_integration, "winreg", registry), patch.object(
            windows_integration.sys, "platform", "win32"
        ):
            self.assertTrue(WindowsStartup.is_enabled())
        registry.QueryValueEx.assert_called_once_with(key, "StickyOmelet")

    def test_is_enabled_handles_missing_value(self) -> None:
        registry = fake_winreg()
        registry.OpenKey.side_effect = FileNotFoundError
        with patch.object(windows_integration, "winreg", registry), patch.object(
            windows_integration.sys, "platform", "win32"
        ):
            self.assertFalse(WindowsStartup.is_enabled())

    def test_enable_writes_current_executable_command(self) -> None:
        registry = fake_winreg()
        key = object()
        registry.CreateKey.return_value.__enter__.return_value = key
        with patch.object(windows_integration, "winreg", registry), patch.object(
            windows_integration.sys, "platform", "win32"
        ), patch.object(WindowsStartup, "command", return_value=r'"C:\Apps\StickyOmelet.exe"'):
            WindowsStartup.set_enabled(True)
        registry.SetValueEx.assert_called_once_with(
            key, "StickyOmelet", 0, registry.REG_SZ, r'"C:\Apps\StickyOmelet.exe"'
        )

    def test_disable_deletes_value_and_missing_value_is_safe(self) -> None:
        registry = fake_winreg()
        key = object()
        registry.OpenKey.return_value.__enter__.return_value = key
        with patch.object(windows_integration, "winreg", registry), patch.object(
            windows_integration.sys, "platform", "win32"
        ):
            WindowsStartup.set_enabled(False)
        registry.DeleteValue.assert_has_calls([call(key, "StickyOmelet"), call(key, "StickyDot")])

        registry.OpenKey.side_effect = FileNotFoundError
        with patch.object(windows_integration, "winreg", registry), patch.object(
            windows_integration.sys, "platform", "win32"
        ):
            WindowsStartup.set_enabled(False)

    def test_enable_removes_previous_brand_value(self) -> None:
        registry = fake_winreg()
        key = object()
        registry.CreateKey.return_value.__enter__.return_value = key
        registry.OpenKey.return_value.__enter__.return_value = key
        with patch.object(windows_integration, "winreg", registry), patch.object(
            windows_integration.sys, "platform", "win32"
        ), patch.object(WindowsStartup, "command", return_value=r'"C:\Apps\StickyOmelet.exe"'):
            WindowsStartup.set_enabled(True)
        registry.DeleteValue.assert_called_once_with(key, "StickyDot")

    def test_is_enabled_honours_previous_brand_value(self) -> None:
        registry = fake_winreg()
        key = object()
        registry.OpenKey.return_value.__enter__.return_value = key

        def query(_key: object, name: str) -> tuple[str, int]:
            if name == "StickyDot":
                return (r'"C:\Apps\StickyDot.exe"', registry.REG_SZ)
            raise FileNotFoundError

        registry.QueryValueEx.side_effect = query
        with patch.object(windows_integration, "winreg", registry), patch.object(
            windows_integration.sys, "platform", "win32"
        ):
            self.assertTrue(WindowsStartup.is_enabled())

    def test_migrate_legacy_entry_rewrites_old_value_under_current_name(self) -> None:
        registry = fake_winreg()
        key = object()
        registry.OpenKey.return_value.__enter__.return_value = key
        registry.CreateKey.return_value.__enter__.return_value = key

        def query(_key: object, name: str) -> tuple[str, int]:
            if name == "StickyDot":
                return (r'"C:\Apps\StickyDot.exe"', registry.REG_SZ)
            raise FileNotFoundError

        registry.QueryValueEx.side_effect = query
        with patch.object(windows_integration, "winreg", registry), patch.object(
            windows_integration.sys, "platform", "win32"
        ), patch.object(WindowsStartup, "command", return_value=r'"C:\Apps\StickyOmelet.exe"'):
            WindowsStartup.migrate_legacy_entry()
        registry.SetValueEx.assert_called_once_with(
            key, "StickyOmelet", 0, registry.REG_SZ, r'"C:\Apps\StickyOmelet.exe"'
        )
        registry.DeleteValue.assert_called_once_with(key, "StickyDot")

    def test_migrate_legacy_entry_leaves_current_value_alone(self) -> None:
        registry = fake_winreg()
        key = object()
        registry.OpenKey.return_value.__enter__.return_value = key
        registry.QueryValueEx.return_value = (r'"C:\Apps\StickyOmelet.exe"', registry.REG_SZ)
        with patch.object(windows_integration, "winreg", registry), patch.object(
            windows_integration.sys, "platform", "win32"
        ):
            WindowsStartup.migrate_legacy_entry()
        registry.SetValueEx.assert_not_called()
        registry.DeleteValue.assert_not_called()

    def test_non_windows_platform_is_safe(self) -> None:
        with patch.object(windows_integration.sys, "platform", "linux"):
            self.assertFalse(WindowsStartup.is_enabled())
            with self.assertRaisesRegex(RuntimeError, "only on Windows"):
                WindowsStartup.set_enabled(True)


if __name__ == "__main__":
    unittest.main()
