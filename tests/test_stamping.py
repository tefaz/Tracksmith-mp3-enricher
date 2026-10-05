from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from test_ui import window_for

from song_metadata_enricher.model import LyricLine, Word
from song_metadata_enricher.tags import read_track
from song_metadata_enricher.timing_data import pending_line_at


def test_pending_target_is_missing_line_between_audio_anchors():
    lines = [
        LyricLine("Already timed first", 19.16, 20.74, words=[Word("First", 19.16, 20.74)]),
        LyricLine("Missing second"),
        LyricLine("Already timed third", 23.68, 25),
        LyricLine("Missing fourth"),
    ]
    original = deepcopy(lines)
    assert pending_line_at(lines, 21, 230) == 1
    assert pending_line_at(lines, 23.68, 230) == 3
    assert pending_line_at(lines, 26, 230) == 3
    assert pending_line_at(lines, 15, 230) is None
    assert lines == original


def test_pending_target_never_uses_already_timed_or_passed_lines():
    lines = [
        LyricLine("Deleted intro"),
        LyricLine("Known", 1),
        LyricLine("Missing"),
        LyricLine("Next known", 2),
    ]
    assert pending_line_at(lines, 0.5, 4) == 0
    assert pending_line_at(lines, 1.5, 4) == 2
    assert pending_line_at(lines, 3, 4) is None
    assert pending_line_at([LyricLine("Timed", 1)], 2, 4) is None


def stamping_window(qtbot, mp3, tmp_path):
    window = window_for(qtbot, mp3, tmp_path)
    window.audio_output.setMuted(True)
    qtbot.waitUntil(lambda: window.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia)
    window.track.aligned_lines = [
        LyricLine("Known first", 0.2, 0.4, 0.9, "ai", [Word("First", 0.2, 0.4, 0.9)]),
        LyricLine("Known second", 0.6, 0.85, 0.9, "ai", [Word("Second", 0.6, 0.85, 0.9)]),
        LyricLine("Missing third"),
        LyricLine("Known fourth", 2.5, 2.7, 0.9, "ai", [Word("Fourth", 2.5, 2.7, 0.9)]),
        LyricLine("Missing fifth"),
    ]
    window.render_table()
    window.activateWindow()
    window.table.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    return window


def test_real_playback_enter_stamps_current_missing_line_and_keeps_first_words(
    qtbot, mp3, tmp_path
):
    window = stamping_window(qtbot, mp3, tmp_path)
    original = deepcopy(window.track.aligned_lines)
    window.table.selectRow(0)  # Reproduce stale initial selection.
    window.player.setPosition(1050)
    window.player.play()
    qtbot.waitUntil(lambda: window.player.position() > 1150 and window.table.currentRow() == 2)
    assert window.stamp_target() == 2
    qtbot.keyClick(window.table, Qt.Key.Key_Return)
    window.player.pause()
    assert 1.05 < window.track.aligned_lines[2].start < 2.5
    assert window.track.aligned_lines[:2] == original[:2]
    assert window.track.aligned_lines[3] == original[3]
    assert (
        window.table.currentRow() == 2
    )  # Stay in this audio section until playback reaches the fourth.
    window.undo_action.trigger()
    assert window.track.aligned_lines == original
    assert window.table.currentRow() == 2
    assert not read_track(mp3).aligned_lines
    window.close()


def test_enter_cannot_overwrite_even_deliberately_selected_first_line(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    first = deepcopy(window.track.aligned_lines[0])
    window.table.selectRow(0)
    window.seek_line(0)  # Deliberate selection is retained for explicit correction.
    window.player.setPosition(1100)
    qtbot.keyClick(window.table, Qt.Key.Key_Return)
    assert window.track.aligned_lines[0] == first
    assert window.track.aligned_lines[2].start is None  # Enter never targets a different row.
    window.table.selectRow(2)
    qtbot.keyClick(window.table, Qt.Key.Key_Return)
    assert window.track.aligned_lines[2].start == 1.1
    window.close()


def test_clear_and_restamp_replaces_line_and_undo_restores_word_data(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    original = deepcopy(window.track.aligned_lines)
    window.table.selectRow(0)
    window.seek_line(0)
    window.unmatch_button.click()
    assert window.track.aligned_lines[0].start is None
    assert not window.track.aligned_lines[0].words
    window.player.setPosition(500)
    qtbot.keyClick(window.table, Qt.Key.Key_Return)
    assert window.track.aligned_lines[0].start == 0.5
    assert window.track.aligned_lines[2].start is None
    window.undo_action.trigger()
    assert window.track.aligned_lines[0].start is None
    window.undo_action.trigger()
    assert window.track.aligned_lines == original
    window.close()


def test_enter_and_removed_retime_shortcut_cannot_overwrite_finished_timing(
    qtbot, mp3, tmp_path
):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.track.aligned_lines = [
        LyricLine("Already timed", 0.2, 0.4, 0.9, "ai", [Word("First", 0.2, 0.4, 0.9)])
    ]
    window.render_table()
    original = deepcopy(window.track.aligned_lines)
    window.player.setPosition(1100)
    qtbot.keyClick(window.table, Qt.Key.Key_Return)
    assert window.track.aligned_lines == original
    assert not window.stamp_button.isEnabled()
    assert not window._stamp_history
    window.player.setPosition(3950)
    qtbot.keyClick(window.table, Qt.Key.Key_Return, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.track.aligned_lines == original
    assert not window._stamp_history
    window.close()


def test_pinned_untimed_line_cannot_be_stamped_outside_neighbour_interval(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.table.selectRow(2)
    window.seek_line(2)
    window.player.setPosition(3000)
    original = deepcopy(window.track.aligned_lines)
    window.stamp_line()
    assert window.track.aligned_lines == original
    assert not window.stamp_button.isEnabled()
    assert "Seek there before stamping" in window.statusBar().currentMessage()
    window.close()


def test_stamp_undo_is_per_song_and_does_not_discard_later_word_edits(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.player.setPosition(1100)
    window.table.selectRow(2)
    window.stamp_line()
    first = window.track
    second_path = tmp_path / "Second.mp3"
    second_path.write_bytes(mp3.read_bytes())
    window.load_track(read_track(second_path))
    assert not window.undo_action.isEnabled()
    window.song_list.setCurrentRow(0)
    assert window.track is first
    assert window.undo_action.isEnabled()
    first.aligned_lines[2].words = [Word("New correction", 1.1, 1.3, source="manual")]
    original = deepcopy(first.aligned_lines)
    window.undo_stamp()
    assert first.aligned_lines == original
    assert "draft changed" in window.statusBar().currentMessage()
    window.close()


def test_holding_enter_does_not_stamp_multiple_lines(qtbot, mp3, tmp_path):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    window = stamping_window(qtbot, mp3, tmp_path)
    window.player.setPosition(1100)
    original = deepcopy(window.track.aligned_lines)
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r", True
    )
    QApplication.sendEvent(window.table, event)
    assert window.track.aligned_lines == original
    assert not window._stamp_history
    window.close()


def test_stamp_stays_on_current_line_and_playback_follows_timed_lines(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.table.selectRow(2)
    window.player.setPosition(1100)
    window.stamp_line()
    assert window.track.aligned_lines[2].start == 1.1
    assert window.table.currentRow() == 2  # Do not jump to the later untimed fifth line.
    window.player.play()
    assert window.table.currentRow() == 2
    window.player.setPosition(2550)
    assert window.table.currentRow() == 3  # Follow the already timed fourth line.
    window.player.pause()
    window.close()


@pytest.mark.parametrize(
    "control", ["play_button", "timeline", "volume", "song_list", "words_button"]
)
def test_enter_stamps_after_focusing_other_controls(qtbot, mp3, tmp_path, control):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.table.selectRow(2)
    window.player.setPosition(1100)
    widget = getattr(window, control)
    widget.setFocus()
    clicked = []
    if hasattr(widget, "clicked"):
        widget.clicked.connect(lambda: clicked.append(True))
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert window.track.aligned_lines[2].start == 1.1
    assert not window.is_playing()
    assert not clicked
    # A second press never overwrites timing or activates the focused control.
    window.player.setPosition(1200)
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert window.track.aligned_lines[2].start == 1.1
    assert not clicked
    window.close()


@pytest.mark.parametrize("control", ["table", "play_button", "volume"])
def test_delete_clears_current_line_and_undo_restores_words(qtbot, mp3, tmp_path, control):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.table.selectRow(1)
    original = deepcopy(window.track.aligned_lines)
    widget = getattr(window, control)
    widget.setFocus()
    qtbot.keyClick(widget, Qt.Key.Key_Delete)
    assert window.track.aligned_lines[1].start is None
    assert not window.track.aligned_lines[1].words
    assert window.track.aligned_lines[0] == original[0]
    assert window.track.aligned_lines[2:] == original[2:]
    window.undo_stamp()
    assert window.track.aligned_lines == original
    window.close()


def test_delete_preserves_typing_and_playback_lock(qtbot, mp3, tmp_path):
    window = stamping_window(qtbot, mp3, tmp_path)
    window.table.selectRow(0)
    original = deepcopy(window.track.aligned_lines)
    edit = window.metadata_fields["title"]
    edit.setText("ABC")
    edit.setCursorPosition(0)
    edit.setFocus()
    qtbot.keyClick(edit, Qt.Key.Key_Delete)
    assert edit.text() == "BC"
    assert window.track.aligned_lines == original
    window.player.setPosition(300)
    window.player.play()
    window.play_button.setFocus()
    assert not window.unmatch_button.isEnabled()
    qtbot.keyClick(window.play_button, Qt.Key.Key_Delete)
    assert window.track.aligned_lines == original
    window.player.pause()
    window.close()
