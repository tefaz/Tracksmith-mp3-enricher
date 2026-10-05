from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog
from test_ui import window_for

from tracksmith.config import Settings
from tracksmith.model import LyricLine, Word
from tracksmith.theme import ToggleSwitch
from tracksmith.ui import MainWindow, SettingsDialog


def test_welcome_load_and_next_step_follow_lyrics_state(qtbot, mp3, tmp_path, monkeypatch):
    window = MainWindow(Settings(cache_directory=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    window.show()
    assert window.workspace_stack.currentIndex() == 0
    assert window.welcome_load_button.isVisible()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(mp3)], ""))
    qtbot.mouseClick(window.welcome_load_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.track is not None and window.job is None)
    assert window.workspace_stack.currentIndex() == 1
    assert "paste lyrics" in window.next_step.text()
    assert not window.save_button.isEnabled()
    window.lyrics_editor.setPlainText("First line\nSecond line")
    assert "Lyrics changed" in window.next_step.text()
    assert window.save_state.text() == "Edits not saved"
    assert window.save_button.isEnabled()
    window.apply_button.click()
    assert window.table.rowCount() == 2
    assert "Analyze your audio" in window.next_step.text()
    window.confirm_discard = lambda: True
    window.close()


def review_window(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.track.aligned_lines = [
        LyricLine("Missing"),
        LyricLine("Uncertain", 0.8, confidence=0.5, source="ai"),
        LyricLine("Confident", 1.2, confidence=0.95, source="ai", line_reviewed=True),
        LyricLine("Manual", 1.6, confidence=1, source="manual", line_reviewed=True),
        LyricLine(
            "Estimated",
            2,
            confidence=1,
            source="manual",
            words=[Word("Estimated", 2, 2.4, source="estimated")],
        ),
    ]
    window.render_table()
    return window


def test_review_filter_and_navigation_preserve_timing_data(qtbot, mp3, tmp_path):
    window = review_window(qtbot, mp3, tmp_path)
    original = deepcopy(window.track.aligned_lines)
    window.table.selectRow(2)
    window.review_filter.setChecked(True)
    assert [row for row in range(5) if not window.table.isRowHidden(row)] == [0, 1, 4]
    assert window.table.currentRow() == 0
    window.table.selectRow(1)
    window.seek_line(1)
    assert window.table.currentRow() == 1
    window.table.selectRow(4)
    window.seek_line(4)
    assert window.table.currentRow() == 4
    window.table.selectRow(0)
    assert window.table.currentRow() == 0
    window.review_filter.setChecked(False)
    assert all(not window.table.isRowHidden(row) for row in range(5))
    assert window.track.aligned_lines == original
    window.close()


def test_hide_reviewed_lines_requires_line_and_word_review(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.track.aligned_lines = [
        LyricLine("Line only", 0, line_reviewed=True),
        LyricLine(
            "Words pending", 0.5, line_reviewed=True,
            words=[Word("Words", 0.5, 0.7), Word("pending", 0.7, 0.9)],
        ),
        LyricLine(
            "Line pending", 1, words_reviewed=True,
            words=[Word("Line", 1, 1.2), Word("pending", 1.2, 1.4)],
        ),
        LyricLine(
            "Both reviewed", 1.5, line_reviewed=True, words_reviewed=True,
            words=[Word("Both", 1.5, 1.7), Word("reviewed", 1.7, 1.9)],
        ),
        LyricLine("Missing", line_reviewed=True, words_reviewed=True),
        LyricLine("Not sung", excluded=True),
    ]
    original = deepcopy(window.track.aligned_lines)
    window.render_table()
    assert window.review_filter.text() == "Hide reviewed lines"
    window.review_filter.setChecked(True)
    assert [row for row in range(6) if not window.table.isRowHidden(row)] == [1, 2, 4]
    window.review_filter.setChecked(False)
    assert all(not window.table.isRowHidden(row) for row in range(6))
    assert window.track.aligned_lines == original
    window.close()


def test_review_keyboard_skips_hidden_rows_and_all_done_has_an_empty_state(qtbot, mp3, tmp_path):
    window = review_window(qtbot, mp3, tmp_path)
    window.review_filter.setChecked(True)
    window.activateWindow()
    window.table.selectRow(0)
    window.table.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    qtbot.keyClick(window.table, Qt.Key.Key_Down)
    assert window.table.currentRow() == 1
    qtbot.keyClick(window.table, Qt.Key.Key_Down)
    assert window.table.currentRow() == 4
    qtbot.keyClick(window.table, Qt.Key.Key_Up)
    assert window.table.currentRow() == 1
    window.track.aligned_lines = [
        LyricLine("Reviewed", 1, confidence=1, source="manual", line_reviewed=True)
    ]
    window.render_table()
    assert window.review_empty.isVisible()
    assert not window.words_button.isEnabled()
    window.review_filter.setChecked(False)
    assert not window.table.isRowHidden(0)
    assert not window.review_empty.isVisible()
    window.close()


def test_switch_works_with_keyboard_label_click_and_disabled_state(qtbot):
    switch = ToggleSwitch("Review only")
    qtbot.addWidget(switch)
    switch.resize(switch.sizeHint())
    switch.show()
    switch.activateWindow()
    switch.setFocus()
    with qtbot.waitSignal(switch.toggled):
        qtbot.keyClick(switch, Qt.Key.Key_Space)
    assert switch.isChecked()
    with qtbot.waitSignal(switch.toggled):
        qtbot.mouseClick(switch, Qt.MouseButton.LeftButton, pos=switch.rect().center())
    assert not switch.isChecked()
    switch.setEnabled(False)
    qtbot.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert not switch.isChecked()


def test_settings_switches_round_trip_without_losing_backend_choices(qtbot, tmp_path):
    original = Settings(cache_directory=str(tmp_path / "cache"), separate_vocals=True, device="cpu")
    dialog = SettingsDialog(original, None)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.settings() == original
    assert "backup" not in dialog.values
    dialog.values["separate_vocals"].setChecked(False)
    modified = dialog.settings()
    assert not modified.separate_vocals
    assert modified.device == "cpu"
    assert Path(modified.cache_directory) == tmp_path / "cache"
    dialog.reject()


def test_focused_switch_space_controls_audio(qtbot, mp3, tmp_path):
    from PySide6.QtMultimedia import QMediaPlayer

    window = window_for(qtbot, mp3, tmp_path)
    window.activateWindow()
    window.review_filter.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    qtbot.keyClick(window.review_filter, Qt.Key.Key_Space)
    assert not window.review_filter.isChecked()
    assert window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    qtbot.keyClick(window.review_filter, Qt.Key.Key_Space)
    assert window.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
    window.close()


def test_focused_button_space_only_toggles_playback(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.activateWindow()
    window.words_button.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    clicks = []
    window.words_button.clicked.connect(lambda: clicks.append(True))
    qtbot.keyClick(window.words_button, Qt.Key.Key_Space)
    assert window.is_playing()
    qtbot.keyClick(window.words_button, Qt.Key.Key_Space)
    assert not window.is_playing()
    assert clicks == []
    window.close()


def test_dark_mode_is_default_and_toggle_is_saved(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    assert window.dark_mode_toggle.isChecked()
    assert window.palette().base().color().lightness() < 128
    qtbot.mouseClick(window.dark_mode_toggle, Qt.MouseButton.LeftButton)
    assert window.palette().base().color().lightness() > 128
    assert window.workspace_store.preferences()["theme"] == "Light"
    qtbot.mouseClick(window.dark_mode_toggle, Qt.MouseButton.LeftButton)
    assert window.palette().base().color().lightness() < 128
    assert window.workspace_store.preferences()["theme"] == "Dark"
    window.close()
