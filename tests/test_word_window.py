from copy import deepcopy

import pytest
from PySide6.QtMultimedia import QMediaPlayer
from test_ui import window_for

from tracksmith.model import LyricLine, Word
from tracksmith.timing_data import word_timing_window
from tracksmith.timing_editor import WordTimingDialog


def test_untimed_second_line_infers_celine_example_window():
    lines = [
        LyricLine("first", 19.16, 20.74, words=[Word("first", 19.16, 20.74)]),
        LyricLine("second line"),
        LyricLine("third", 23.68),
    ]
    window = word_timing_window(lines, 1, 229.832)
    assert (window.start, window.end, window.word_start) == (19.16, 23.68, 20.74)
    assert window.anchored
    lines[0].end = None
    lines[0].words = []
    assert word_timing_window(lines, 1, 229.832).word_start == 19.16


def test_missing_neighbours_use_file_edges_and_no_anchor_does_not_fake_a_start():
    lines = [LyricLine("first"), LyricLine("middle", 5), LyricLine("last")]
    assert word_timing_window(lines, 0, 10).end == 5
    assert word_timing_window(lines, 2, 10).start == 5
    window = word_timing_window([LyricLine("no anchor")], 0, 10)
    assert (window.start, window.end, window.anchored) == (0, 10, False)
    with pytest.raises(ValueError, match="out of order"):
        word_timing_window(
            [LyricLine("before", 9), LyricLine("missing"), LyricLine("after", 5)], 1, 10
        )


def test_main_window_passes_neighbours_even_when_selected_line_is_untimed(
    qtbot, mp3, tmp_path, monkeypatch
):
    window = window_for(qtbot, mp3, tmp_path)
    window.track.aligned_lines = [
        LyricLine("first", 0.5, 0.9),
        LyricLine("Missing two words"),
        LyricLine("third", 2.5),
    ]
    window.render_table()
    window.table.selectRow(1)
    inspected = []

    def inspect(dialog):
        inspected.append(dialog)
        assert (dialog.graph.view_start, dialog.graph.view_end) == (0.5, 2.5)
        assert (dialog.timeline.minimum(), dialog.timeline.maximum()) == (500, 2500)
        assert dialog.drafts[0].start == 0.9
        assert dialog.drafts[-1].end == 2.5
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(WordTimingDialog, "exec", inspect)
    window.words_button.click()
    assert len(inspected) == 1
    assert window.track.aligned_lines[1].start is None
    window.close()


def bounded_dialog(qtbot, mp3, tmp_path, end=2.5):
    parent = window_for(qtbot, mp3, tmp_path)
    parent.audio_output.setMuted(True)
    qtbot.waitUntil(lambda: parent.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia)
    lines = [
        LyricLine("previous", 0.5, 0.6),
        LyricLine("Missing two words"),
        LyricLine("next", end),
    ]
    window = word_timing_window(lines, 1, 4)
    dialog = WordTimingDialog(lines[1], parent.player, 4, parent, next_start=end, window=window)
    qtbot.addWidget(dialog)
    dialog.show()
    return parent, dialog


def test_every_seek_control_and_word_edit_stays_within_window(qtbot, mp3, tmp_path):
    parent, dialog = bounded_dialog(qtbot, mp3, tmp_path)
    assert parent.player.position() == 500
    dialog.seek(-10000)
    assert parent.player.position() == 500
    dialog.seek(10000)
    assert parent.player.position() == 2500
    dialog.graph.seek_requested.emit(-1)
    assert parent.player.position() == 500
    dialog.graph.seek_requested.emit(99)
    assert parent.player.position() == 2500
    dialog.timeline.sliderMoved.emit(0)
    assert parent.player.position() == 500
    dialog.play_lead_in()
    assert 500 <= parent.player.position() < 2500
    parent.player.pause()
    old = deepcopy(dialog.drafts)
    dialog.table.item(0, 1).setText("00:00.100")
    assert dialog.drafts == old
    assert "review window" in dialog.message.text()
    dialog.space_evenly()
    dialog.update_window()
    assert (dialog.graph.view_start, dialog.graph.view_end) == (0.5, 2.5)
    assert dialog.drafts[0].start >= 0.5
    assert dialog.drafts[-1].end <= 2.5
    dialog.reject()
    parent.player.setPosition(0)
    qtbot.wait(30)
    assert parent.player.position() == 0  # Window restriction ends when editor closes.
    parent.close()


def test_real_playback_pauses_at_section_end_and_can_replay_section(qtbot, mp3, tmp_path):
    parent, dialog = bounded_dialog(qtbot, mp3, tmp_path, end=0.9)
    parent.player.setPosition(750)
    dialog.toggle_play()
    qtbot.waitUntil(
        lambda: parent.player.playbackState() == QMediaPlayer.PlaybackState.PausedState,
        timeout=3000,
    )
    assert parent.player.position() == 900
    qtbot.wait(100)
    assert parent.player.position() == 900
    dialog.toggle_play()
    assert 500 <= parent.player.position() < 900
    parent.player.pause()
    parent.player.setPosition(3500)
    assert parent.player.position() == 900
    parent.player.setPosition(0)
    assert parent.player.position() == 500
    dialog.reject()
    parent.close()


def test_space_in_word_dialog_controls_playback_from_buttons_and_switches(qtbot, mp3, tmp_path):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    parent, dialog = bounded_dialog(qtbot, mp3, tmp_path)
    dialog.activateWindow()
    qtbot.waitUntil(dialog.isActiveWindow)
    clicks = []
    dialog.undo_button.clicked.connect(lambda: clicks.append(True))
    for widget in (dialog.undo_button, dialog.join_check, dialog.word_start, dialog.timeline):
        widget.setFocus()
        joined = dialog.join_check.isChecked()
        qtbot.keyClick(widget, Qt.Key.Key_Space)
        assert parent.is_playing()
        QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ", True))
        assert parent.is_playing()
        qtbot.keyClick(widget, Qt.Key.Key_Space)
        assert not parent.is_playing()
        assert dialog.join_check.isChecked() == joined
    assert not clicks
    dialog.reject()
    parent.close()
