from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import keep_sync
from keep_sync import KeepSyncClient, SettingsStore, WindowsSecret


class FakeColor:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeColorValue:
    White = FakeColor("White")
    Yellow = FakeColor("Yellow")
    Green = FakeColor("Green")
    Teal = FakeColor("Teal")
    Blue = FakeColor("Blue")
    DarkBlue = FakeColor("DarkBlue")
    Purple = FakeColor("Purple")
    Pink = FakeColor("Pink")
    Red = FakeColor("Red")
    Orange = FakeColor("Orange")
    Brown = FakeColor("Brown")
    Gray = FakeColor("Gray")


class FakeLabels:
    def __init__(self, *names: str) -> None:
        self._labels = [type("Label", (), {"name": name})() for name in names]

    def all(self) -> list[object]:
        return self._labels


class FakeEntry:
    def __init__(self, text: str, checked: bool = False, deleted: bool = False) -> None:
        self.text = text
        self.checked = checked
        self.deleted = deleted

    def delete(self) -> None:
        self.deleted = True


class FakeNote:
    def __init__(
        self,
        note_id: str,
        title: str = "",
        text: str = "",
        *,
        updated: datetime | None = None,
        pinned: bool = False,
        color: str = "White",
        labels: tuple[str, ...] = (),
    ) -> None:
        self.id = note_id
        self.title = title
        self.text = text
        self.deleted = False
        self.trashed = False
        self.archived = False
        self.pinned = pinned
        self.color = FakeColor(color)
        self.labels = FakeLabels(*labels)
        self.timestamps = type(
            "Timestamps",
            (),
            {"updated": updated or datetime.now(timezone.utc)},
        )()

    def trash(self) -> None:
        self.trashed = True


class FakeList(FakeNote):
    def __init__(self, note_id: str, title: str = "", items: list[FakeEntry] | None = None, **kwargs: object) -> None:
        super().__init__(note_id, title, **kwargs)
        self.items = list(items or [])

    def add(self, text: str, checked: bool) -> FakeEntry:
        entry = FakeEntry(text, checked)
        self.items.append(entry)
        return entry


class FakeKeep:
    def __init__(self, items: list[FakeNote] | None = None) -> None:
        self.items = list(items or [])
        self.sync_count = 0
        self.auth_args: tuple[object, ...] | None = None

    def authenticate(self, email: str, token: str, *, sync: bool, device_id: str | None) -> None:
        self.auth_args = (email, token, sync, device_id)

    def all(self) -> list[FakeNote]:
        return self.items

    def get(self, note_id: str) -> FakeNote | None:
        return next((item for item in self.items if item.id == note_id), None)

    def sync(self) -> None:
        self.sync_count += 1

    def createNote(self, title: str, text: str) -> FakeNote:  # noqa: N802 - mirrors gkeepapi
        note = FakeNote("new-note", title, text)
        self.items.append(note)
        return note

    def createList(self, title: str, items: list[tuple[str, bool]]) -> FakeList:  # noqa: N802
        note = FakeList("new-list", title, [FakeEntry(text, checked) for text, checked in items])
        self.items.append(note)
        return note


class SettingsStoreTests(unittest.TestCase):
    def test_defaults_are_created_in_StickyOmelet_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                store = SettingsStore()
                self.assertEqual(Path(temporary) / "StickyOmelet" / "settings.json", store.path)
                self.assertEqual(4, store.data["version"])
                self.assertEqual("400x540+80+80", store.data["geometry"])
                store.save()
                self.assertTrue(store.path.exists())

    def test_settings_round_trip_only_loads_known_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                store = SettingsStore()
                store.data["theme"] = "light"
                store.data["bubble_position"] = "120,240"
                store.save()
                raw = json.loads(store.path.read_text(encoding="utf-8"))
                raw["unexpected"] = "ignored"
                store.path.write_text(json.dumps(raw), encoding="utf-8")

                loaded = SettingsStore()
                self.assertEqual("light", loaded.data["theme"])
                self.assertEqual("120,240", loaded.data["bubble_position"])
                self.assertNotIn("unexpected", loaded.data)

    def test_legacy_settings_are_copied_and_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            legacy = base / "JustNotes" / "settings.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "email": "person@example.com",
                        "token_dpapi": "encrypted-value",
                        "geometry": "800x900+12-7",
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                store = SettingsStore()

            self.assertEqual(4, store.data["version"])
            self.assertEqual("400x540+12-7", store.data["geometry"])
            self.assertEqual("encrypted-value", store.data["token_dpapi"])
            self.assertTrue(store.path.exists())

    def test_settings_from_the_stickydot_brand_are_carried_over(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            legacy = base / "StickyDot" / "settings.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(
                json.dumps({"version": 4, "email": "person@example.com", "token_dpapi": "encrypted-value", "bubble_position": "10,20"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                store = SettingsStore()

            self.assertEqual(base / "StickyOmelet" / "settings.json", store.path)
            self.assertEqual("encrypted-value", store.data["token_dpapi"])
            self.assertEqual("10,20", store.data["bubble_position"])
            self.assertTrue(legacy.exists(), "the previous brand's file is copied, not moved")

    def test_invalid_json_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "StickyOmelet"
            folder.mkdir()
            (folder / "settings.json").write_text("{not-json", encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                store = SettingsStore()

            self.assertEqual("dark", store.data["theme"])
            self.assertTrue((folder / "settings.broken.json").exists())

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is available only on Windows")
    def test_dpapi_round_trip(self) -> None:
        secret = "test-token-αβγ"
        protected = WindowsSecret.protect(secret)
        self.assertNotEqual(secret, protected)
        self.assertEqual(secret, WindowsSecret.unprotect(protected))

    def test_empty_secret_stays_empty(self) -> None:
        self.assertEqual("", WindowsSecret.protect(""))
        self.assertEqual("", WindowsSecret.unprotect(""))


class KeepSyncClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.list_patch = patch.object(keep_sync.keep_node, "List", FakeList)
        self.color_patch = patch.object(keep_sync.keep_node, "ColorValue", FakeColorValue)
        self.list_patch.start()
        self.color_patch.start()
        self.addCleanup(self.list_patch.stop)
        self.addCleanup(self.color_patch.stop)

    def client_with(self, *items: FakeNote) -> tuple[KeepSyncClient, FakeKeep]:
        fake = FakeKeep(list(items))
        client = KeepSyncClient()
        client.keep = fake  # type: ignore[assignment]
        return client, fake

    def test_authenticate_strips_values_and_returns_records(self) -> None:
        fake = FakeKeep()
        client = KeepSyncClient()
        with patch.object(keep_sync.gkeepapi, "Keep", return_value=fake):
            records = client.authenticate(" person@example.com ", " token ", "device")

        self.assertEqual([], records)
        self.assertEqual(("person@example.com", "token", True, "device"), fake.auth_args)
        self.assertTrue(client.connected)

    def test_records_filter_and_sort_notes(self) -> None:
        now = datetime.now(timezone.utc)
        recent = FakeNote("recent", "Recent", updated=now, labels=("z", "a"))
        pinned = FakeNote("pinned", "Pinned", updated=now - timedelta(days=2), pinned=True)
        hidden = FakeNote("hidden", "Hidden")
        hidden.trashed = True
        checklist = FakeList("list", "List", [FakeEntry("Open"), FakeEntry("Done", True)])
        client, _ = self.client_with(recent, hidden, pinned, checklist)

        records = client.records()

        self.assertEqual("pinned", records[0].id)
        self.assertNotIn("hidden", {record.id for record in records})
        recent_record = next(record for record in records if record.id == "recent")
        self.assertEqual(("a", "z"), recent_record.labels)
        list_record = next(record for record in records if record.id == "list")
        self.assertTrue(list_record.is_list)
        self.assertEqual("☐ Open\n☑ Done", list_record.body)

    def test_parse_list_body_supports_all_documented_markers(self) -> None:
        parsed = KeepSyncClient._parse_list_body(
            "☐ Milk\n☑ Eggs\n[x] Bread\n[ ] Tea\n- Apples\n* Pears\n\n"
        )
        self.assertEqual(
            [
                ("Milk", False),
                ("Eggs", True),
                ("Bread", True),
                ("Tea", False),
                ("Apples", False),
                ("Pears", False),
            ],
            parsed,
        )

    def test_update_list_reuses_adds_and_deletes_entries(self) -> None:
        checklist = FakeList("list", items=[FakeEntry("Old 1"), FakeEntry("Old 2"), FakeEntry("Extra")])
        client, fake = self.client_with(checklist)

        client.update_note("list", "Renamed", "☐ First\n☑ Second\n- Third\n[x] Fourth")

        self.assertEqual("Renamed", checklist.title)
        active = [(entry.text, entry.checked) for entry in checklist.items if not entry.deleted]
        self.assertEqual(
            [("First", False), ("Second", True), ("Third", False), ("Fourth", True)],
            active,
        )
        self.assertEqual(1, fake.sync_count)

    def test_update_text_note(self) -> None:
        note = FakeNote("note", "Old", "Before")
        client, fake = self.client_with(note)
        client.update_note("note", "New", "After")
        self.assertEqual(("New", "After"), (note.title, note.text))
        self.assertEqual(1, fake.sync_count)

    def test_create_note_and_list_use_timestamp_titles(self) -> None:
        client, fake = self.client_with()

        _, note_id = client.create_note()
        _, list_id = client.create_list()

        self.assertEqual("new-note", note_id)
        self.assertEqual("new-list", list_id)
        self.assertRegex(fake.items[0].title, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertRegex(fake.items[1].title, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual("New item", fake.items[1].items[0].text)  # type: ignore[attr-defined]

    def test_note_actions_sync(self) -> None:
        note = FakeNote("note", color="White")
        client, fake = self.client_with(note)

        client.toggle_pin("note")
        self.assertTrue(note.pinned)
        client.cycle_color("note")
        self.assertEqual("Yellow", note.color.name)
        client.trash_note("note")
        self.assertTrue(note.trashed)
        self.assertEqual(3, fake.sync_count)

    def test_missing_or_disconnected_note_has_clear_error(self) -> None:
        client = KeepSyncClient()
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            client.records()

        client, _ = self.client_with()
        with self.assertRaisesRegex(RuntimeError, "no longer exists"):
            client.update_note("missing", "Title", "Body")


if __name__ == "__main__":
    unittest.main()
