from __future__ import annotations

import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event, get_ident
from unittest.mock import Mock, patch

from keep_sync import NoteRecord
from notes_widget import (
    NotesWidget,
    Palette,
    UndoHistory,
    clamp_bubble_position,
    clamp_bubble_to_work_area,
    clamp_window_geometry,
    clamp_window_position,
    clamp_window_size,
    friendly_time,
    note_title,
    selection_colors,
)


class FakeWidget:
    def configure(self, **_options: object) -> None:
        pass


class MainThreadRoot:
    def __init__(self) -> None:
        self.callbacks: list[object] = []
        self.calling_threads: list[int] = []

    def after(self, _delay: int, callback: object) -> None:
        self.calling_threads.append(get_ident())
        self.callbacks.append(callback)

    def run_pending(self) -> None:
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()


def record(title: str = "", body: str = "") -> NoteRecord:
    return NoteRecord(
        id="id",
        title=title,
        body=body,
        color="White",
        pinned=False,
        is_list=False,
        updated=datetime.now(timezone.utc),
        labels=(),
    )


class UiHelperTests(unittest.TestCase):
    def tearDown(self) -> None:
        Palette.apply_theme("dark")

    def test_note_title_prefers_title_then_first_body_line(self) -> None:
        self.assertEqual("Explicit", note_title(record("  Explicit  ", "Body")))
        self.assertEqual("First item", note_title(record(body="☑ First item\nSecond")))
        self.assertEqual("Untitled note", note_title(record()))

    def test_note_title_is_limited_for_cards(self) -> None:
        self.assertEqual(55, len(note_title(record(body="x" * 80))))

    def test_apply_theme_updates_palette_and_rejects_unknown_names(self) -> None:
        self.assertEqual("light", Palette.apply_theme("light"))
        self.assertEqual("#F4F7FB", Palette.BG)
        self.assertEqual("dark", Palette.apply_theme("unknown"))
        self.assertEqual("#0D1422", Palette.BG)

    def test_friendly_time_ranges(self) -> None:
        fixed = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
        with patch("notes_widget.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed
            self.assertEqual("now", friendly_time(fixed - timedelta(seconds=30)))
            self.assertEqual("5m", friendly_time(fixed - timedelta(minutes=5)))
            two_hours_ago = fixed - timedelta(hours=2)
            self.assertEqual(two_hours_ago.astimezone().strftime("%H:%M"), friendly_time(two_hours_ago))
            self.assertEqual("Yesterday", friendly_time(fixed - timedelta(days=1)))
            ten_days_ago = fixed - timedelta(days=10)
            self.assertEqual(ten_days_ago.astimezone().strftime("%d %b"), friendly_time(ten_days_ago))

    def test_window_size_clamps_to_the_supported_compact_size(self) -> None:
        self.assertEqual((340, 420), clamp_window_size(200, 300))
        self.assertEqual((515, 680), clamp_window_size(515, 680))

    def test_bubble_position_stays_fully_on_screen(self) -> None:
        self.assertEqual((0, 0), clamp_bubble_position(-20, -30, 1920, 1080))
        self.assertEqual((1856, 1016), clamp_bubble_position(1900, 1070, 1920, 1080))
        self.assertEqual((600, 500), clamp_bubble_position(600, 500, 1920, 1080))

    def test_bubble_position_stays_in_secondary_monitor_work_area(self) -> None:
        work_area = (-1280, 40, 0, 984)
        self.assertEqual((-1280, 40), clamp_bubble_to_work_area(-1400, 0, work_area))
        self.assertEqual((-64, 920), clamp_bubble_to_work_area(40, 1000, work_area))
        self.assertEqual((-600, 400), clamp_bubble_to_work_area(-600, 400, work_area))

    def test_restored_window_stays_inside_monitor_work_area(self) -> None:
        work_area = (0, 0, 2048, 1104)
        self.assertEqual("385x497+238+607", clamp_window_geometry("385x497+238+686", work_area))
        self.assertEqual("400x540+0+0", clamp_window_geometry("400x540-80-40", work_area))
        self.assertEqual("400x540+80+80", clamp_window_geometry("400x540+80+80", work_area))

    def test_dragged_window_cannot_be_pushed_off_the_work_area(self) -> None:
        work_area = (0, 0, 2048, 1104)
        self.assertEqual((0, 0), clamp_window_position(-300, -200, 400, 540, work_area))
        self.assertEqual((1648, 564), clamp_window_position(3000, 2000, 400, 540, work_area))
        self.assertEqual((238, 607), clamp_window_position(238, 607, 385, 497, work_area))

    def test_window_wider_than_the_work_area_stays_pinned_to_its_left_edge(self) -> None:
        work_area = (-1280, 40, 0, 984)
        self.assertEqual((-1280, 40), clamp_window_position(-900, 300, 1600, 1200, work_area))

    def test_invalid_saved_geometry_is_left_unchanged(self) -> None:
        self.assertEqual("not-a-geometry", clamp_window_geometry("not-a-geometry", (0, 0, 1920, 1080)))

    def test_selected_text_stays_black_in_both_themes(self) -> None:
        Palette.apply_theme("dark")
        self.assertEqual("#000000", selection_colors()["selectforeground"])
        self.assertEqual(Palette.ACCENT_SOFT, selection_colors()["selectbackground"])
        Palette.apply_theme("light")
        self.assertEqual("#000000", selection_colors()["selectforeground"])
        self.assertEqual(Palette.ACCENT_SOFT, selection_colors()["selectbackground"])

    def test_project_website_opens_from_brand_action(self) -> None:
        widget = NotesWidget.__new__(NotesWidget)
        with patch("notes_widget.webbrowser.open") as open_browser:
            widget.open_project_website()
        open_browser.assert_called_once_with("https://justdataplease.com")

    def test_background_operation_returns_to_tk_on_the_main_thread(self) -> None:
        main_thread = get_ident()
        root = MainThreadRoot()
        executor = ThreadPoolExecutor(max_workers=1)
        started = Event()
        release = Event()
        finished = Event()
        results: list[str] = []
        widget = NotesWidget.__new__(NotesWidget)
        widget.root = root
        widget.executor = executor
        widget.status = FakeWidget()
        widget.sync_button = FakeWidget()
        widget.sync_identity = FakeWidget()
        widget.client = type("Client", (), {"connected": True, "email": "writer@example.com"})()
        widget.pending_operations = 0
        widget.closing = False

        def operation() -> str:
            started.set()
            release.wait(timeout=2)
            finished.set()
            return "saved"

        try:
            widget._run(operation, results.append)
            self.assertTrue(started.wait(timeout=2))
            release.set()
            self.assertTrue(finished.wait(timeout=2))
            while root.callbacks:
                root.run_pending()
        finally:
            executor.shutdown(wait=True)

        self.assertEqual(["saved"], results)
        self.assertEqual([main_thread], list(set(root.calling_threads)))


class SyncCompletionTests(unittest.TestCase):
    def _widget(self) -> NotesWidget:
        widget = NotesWidget.__new__(NotesWidget)
        widget.root = MainThreadRoot()
        widget.client = Mock(connected=True, email="writer@example.com")
        widget.status = Mock()
        widget.sync_button = Mock()
        widget.sync_identity = Mock()
        widget.pending_operations = 0
        widget.closing = False
        widget.save_job = None
        widget.current_mode = "note"
        widget.selected_note = record("First", "Original")
        widget.records = [widget.selected_note]
        widget.editor_revision = 1
        widget.title_var = Mock()
        widget.title_var.get.return_value = "First"
        widget.body_text = Mock()
        widget.body_text.get.return_value = "Edited"
        return widget

    def test_background_sync_error_leaves_ui_available_and_next_operation_runs(self) -> None:
        for minimized in (False, True):
            with self.subTest(minimized=minimized):
                widget = self._widget()
                widget.minimized_to_bubble = minimized
                failed = Future()
                failed.set_exception(RuntimeError("Connection unavailable"))
                succeeded = Future()
                succeeded.set_result("saved")
                widget.executor = Mock()
                widget.executor.submit.side_effect = [failed, succeeded]
                results = []
                with patch("notes_widget.messagebox.showerror") as dialog:
                    widget._run(lambda: None, results.append)
                    widget.root.run_pending()
                    dialog.assert_not_called()
                self.assertEqual(0, widget.pending_operations)
                self.assertEqual([], results)
                self.assertIn("Connection unavailable", widget.status.configure.call_args.kwargs["text"])
                self.assertIn("Sync error", widget.sync_identity.configure.call_args.kwargs["text"])
                widget._run(lambda: None, results.append)
                widget.root.run_pending()
                self.assertEqual(["saved"], results)
                self.assertEqual(0, widget.pending_operations)

    def test_delayed_save_does_not_change_the_note_being_edited(self) -> None:
        widget = self._widget()
        widget._run = Mock()
        widget.flush_save()
        saved = widget._run.call_args.args[1]
        saved_first = replace(widget.selected_note, body="Edited")
        second = replace(record("Second", "Other body"), id="second")
        widget.selected_note = second
        widget.title_var.get.return_value = "Second"
        widget.body_text.get.return_value = "Second edit"
        widget.status.reset_mock()
        saved([saved_first, second])
        self.assertIs(second, widget.selected_note)
        self.assertEqual([saved_first, second], widget.records)
        widget.status.configure.assert_not_called()
        widget.flush_save()
        operation = widget._run.call_args.args[0]
        operation()
        widget.client.update_note.assert_called_once_with("second", "Second", "Second edit")

    def test_delayed_save_does_not_reselect_a_cleared_note(self) -> None:
        widget = self._widget()
        widget._run = Mock()
        widget.flush_save()
        saved = widget._run.call_args.args[1]
        widget.selected_note = None
        saved([record("First", "Edited")])
        self.assertIsNone(widget.selected_note)

    def test_completed_save_updates_current_note_without_replacing_new_typing(self) -> None:
        widget = self._widget()
        widget._run = Mock()
        widget.flush_save()
        saved = widget._run.call_args.args[1]
        widget.editor_revision += 1
        widget.body_text.get.return_value = "Edited some more"
        widget.status.reset_mock()
        refreshed = replace(widget.selected_note, body="Edited")
        saved([refreshed])
        self.assertIs(refreshed, widget.selected_note)
        widget.body_text.delete.assert_not_called()
        widget.body_text.insert.assert_not_called()
        widget.status.configure.assert_not_called()
        widget.flush_save()
        widget._run.call_args.args[0]()
        widget.client.update_note.assert_called_once_with("id", "First", "Edited some more")

    def test_completed_save_updates_current_note_and_reports_success(self) -> None:
        widget = self._widget()
        widget._run = Mock()
        widget.flush_save()
        saved = widget._run.call_args.args[1]
        refreshed = replace(widget.selected_note, body="Edited")
        saved([refreshed])
        self.assertIs(refreshed, widget.selected_note)
        self.assertIn("Synced to Google Keep", widget.status.configure.call_args.kwargs["text"])


class UndoHistoryTests(unittest.TestCase):
    def test_undo_and_redo_walk_through_recorded_states(self) -> None:
        history = UndoHistory("start")
        history.record("start one")
        history.record("start one two")
        self.assertEqual("start one", history.undo())
        self.assertEqual("start", history.undo())
        self.assertIsNone(history.undo())
        self.assertEqual("start one", history.redo())
        self.assertEqual("start one two", history.redo())
        self.assertIsNone(history.redo())

    def test_recording_after_undo_drops_the_redo_tail(self) -> None:
        history = UndoHistory("a")
        history.record("ab")
        history.record("abc")
        history.undo()
        history.record("abX")
        self.assertFalse(history.can_redo())
        self.assertEqual("ab", history.undo())

    def test_identical_snapshots_are_ignored(self) -> None:
        history = UndoHistory("same")
        history.record("same")
        history.record("same")
        self.assertFalse(history.can_undo())

    def test_history_keeps_at_least_twenty_steps(self) -> None:
        history = UndoHistory("v0")
        for step in range(1, 41):
            history.record(f"v{step}")
        undone = 0
        while history.can_undo():
            history.undo()
            undone += 1
        self.assertGreaterEqual(undone, 20)

    def test_capacity_drops_the_oldest_states_first(self) -> None:
        history = UndoHistory("v0", capacity=3)
        for step in range(1, 5):
            history.record(f"v{step}")
        self.assertEqual(["v2", "v3", "v4"], history.snapshots)
        self.assertEqual("v4", history.current)


class ClickAwayCollapseTests(unittest.TestCase):
    """A borderless off-top window has no taskbar button, so clicking another
    app must collapse it to the always-on-top dot bubble instead of burying it."""

    def _widget(self, *, topmost: bool, minimized: bool = False, closing: bool = False) -> NotesWidget:
        widget = NotesWidget.__new__(NotesWidget)
        widget.root = MainThreadRoot()
        widget.settings = type("Settings", (), {"data": {"topmost": topmost}})()
        widget.minimized_to_bubble = minimized
        widget.closing = closing
        widget.click_away_job = None
        widget._own_window_was_foreground = True
        return widget

    def test_click_away_schedules_a_collapse_when_not_pinned_on_top(self) -> None:
        widget = self._widget(topmost=False)
        widget._on_root_deactivate(None)
        self.assertEqual(1, len(widget.root.callbacks))

    def test_click_away_is_ignored_while_always_on_top(self) -> None:
        widget = self._widget(topmost=True)
        widget._on_root_deactivate(None)
        self.assertEqual([], widget.root.callbacks)

    def test_click_away_is_ignored_while_already_a_bubble(self) -> None:
        widget = self._widget(topmost=False, minimized=True)
        widget._on_root_deactivate(None)
        self.assertEqual([], widget.root.callbacks)

    def test_switching_to_another_app_collapses_to_the_bubble(self) -> None:
        widget = self._widget(topmost=False)
        calls: list[str] = []
        widget.minimize = lambda: calls.append("minimize")  # type: ignore[method-assign]
        widget._foreground_is_own_window = lambda: False  # type: ignore[method-assign]
        widget._collapse_if_switched_away()
        self.assertEqual(["minimize"], calls)

    def test_clicking_our_own_menu_leaves_the_window_open(self) -> None:
        widget = self._widget(topmost=False)
        calls: list[str] = []
        widget.minimize = lambda: calls.append("minimize")  # type: ignore[method-assign]
        widget._foreground_is_own_window = lambda: True  # type: ignore[method-assign]
        widget._collapse_if_switched_away()
        self.assertEqual([], calls)

    def test_foreground_poll_collapses_when_deactivate_event_is_missed(self) -> None:
        widget = self._widget(topmost=False)
        calls: list[str] = []
        widget.minimize = lambda: calls.append("minimize")  # type: ignore[method-assign]
        widget._foreground_is_own_window = lambda: False  # type: ignore[method-assign]
        widget._monitor_click_away()
        self.assertEqual(["minimize"], calls)
        self.assertEqual(1, len(widget.root.callbacks))

    def test_foreground_poll_does_not_collapse_an_unarmed_background_window(self) -> None:
        widget = self._widget(topmost=False)
        widget._own_window_was_foreground = False
        calls: list[str] = []
        widget.minimize = lambda: calls.append("minimize")  # type: ignore[method-assign]
        widget._foreground_is_own_window = lambda: False  # type: ignore[method-assign]
        widget._monitor_click_away()
        self.assertEqual([], calls)

    def test_foreground_poll_arms_after_the_widget_becomes_active(self) -> None:
        widget = self._widget(topmost=False)
        widget._own_window_was_foreground = False
        foreground = iter((True, False))
        calls: list[str] = []
        widget.minimize = lambda: calls.append("minimize")  # type: ignore[method-assign]
        widget._foreground_is_own_window = lambda: next(foreground)  # type: ignore[method-assign]
        widget._monitor_click_away()
        widget._monitor_click_away()
        self.assertEqual(["minimize"], calls)


if __name__ == "__main__":
    unittest.main()
